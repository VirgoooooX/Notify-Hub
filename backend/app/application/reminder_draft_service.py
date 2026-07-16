from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from app.application.reminder_service import ReminderCreate
from app.domain.clock import Clock, SystemClock
from app.domain.reminder_drafts import (
    InvalidReminderDraftTransition,
    ReminderDraftExpired,
    ReminderDraftNotFound,
    ReminderDraftParseMethod,
    ReminderDraftPermissionDenied,
    ReminderDraftSourceType,
    ReminderDraftStatus,
    validate_reminder_draft_transition,
)
from app.infrastructure.database.base import new_id
from app.infrastructure.database.reminder_draft_models import ReminderDraft
from app.infrastructure.database.reminder_models import Reminder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ReminderCreator(Protocol):
    async def create(self, command: ReminderCreate, *, now: datetime | None = None) -> Reminder: ...


@dataclass(frozen=True, slots=True)
class ReminderDraftCreate:
    source_type: ReminderDraftSourceType
    source_text: str
    parsed_data: dict[str, Any]
    parse_method: ReminderDraftParseMethod
    validation_errors: tuple[str, ...]
    created_by: str
    status: ReminderDraftStatus = ReminderDraftStatus.EDITING
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReminderDraftUpdate:
    parsed_data: dict[str, Any] | None = None
    parse_method: ReminderDraftParseMethod | None = None
    validation_errors: tuple[str, ...] | None = None
    source_text: str | None = None
    status: ReminderDraftStatus | None = None
    expires_at: datetime | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ReminderDraftService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reminders: ReminderCreator,
        *,
        clock: Clock | None = None,
        draft_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if draft_ttl <= timedelta(0):
            raise ValueError("draft_ttl must be positive")
        self._sessions = session_factory
        self._reminders = reminders
        self._clock = clock or SystemClock()
        self._ttl = draft_ttl

    async def create(
        self,
        command: ReminderDraftCreate,
        *,
        now: datetime | None = None,
    ) -> ReminderDraft:
        instant = _utc(now or self._clock.now())
        expires_at = _utc(command.expires_at or instant + self._ttl)
        if expires_at <= instant:
            raise ReminderDraftExpired("reminder draft expiry must be in the future")
        if not command.created_by.strip():
            raise ValueError("created_by is required")
        if command.status not in {
            ReminderDraftStatus.EDITING,
            ReminderDraftStatus.AWAITING_CONFIRMATION,
        }:
            raise InvalidReminderDraftTransition(
                f"cannot create reminder draft in {command.status.value} status"
            )
        if (
            command.status is ReminderDraftStatus.AWAITING_CONFIRMATION
            and command.validation_errors
        ):
            raise ValueError("a draft with validation errors cannot await confirmation")
        draft = ReminderDraft(
            id=new_id("rdr"),
            source_type=command.source_type.value,
            source_text=command.source_text,
            parsed_data=dict(command.parsed_data),
            parse_method=command.parse_method.value,
            validation_errors=list(command.validation_errors),
            status=command.status.value,
            created_by=command.created_by,
            confirmed_reminder_id=None,
            expires_at=expires_at,
            created_at=instant,
            updated_at=instant,
        )
        async with self._sessions() as session, session.begin():
            session.add(draft)
        return draft

    async def update(
        self,
        draft_id: str,
        command: ReminderDraftUpdate,
        *,
        created_by: str,
        now: datetime | None = None,
    ) -> ReminderDraft:
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
            draft = await self._get_owned(session, draft_id, created_by)
            self._expire_if_due(draft, instant)
            if draft.status == ReminderDraftStatus.EXPIRED.value:
                expired = True
            else:
                expired = False
                current = ReminderDraftStatus(draft.status)
                target = command.status or current
                validate_reminder_draft_transition(current, target)
                validation_errors = (
                    list(command.validation_errors)
                    if command.validation_errors is not None
                    else draft.validation_errors
                )
                if target is ReminderDraftStatus.AWAITING_CONFIRMATION and validation_errors:
                    raise ValueError("a draft with validation errors cannot await confirmation")
                if command.parsed_data is not None:
                    draft.parsed_data = dict(command.parsed_data)
                if command.parse_method is not None:
                    draft.parse_method = command.parse_method.value
                if command.validation_errors is not None:
                    draft.validation_errors = validation_errors
                if command.source_text is not None:
                    draft.source_text = command.source_text
                if command.expires_at is not None:
                    expires_at = _utc(command.expires_at)
                    if expires_at <= instant:
                        raise ReminderDraftExpired("reminder draft expiry must be in the future")
                    draft.expires_at = expires_at
                draft.status = target.value
                draft.updated_at = instant
        if expired:
            raise ReminderDraftExpired(draft_id)
        return draft

    async def get_for_confirmation(
        self,
        draft_id: str,
        *,
        created_by: str,
        now: datetime | None = None,
    ) -> ReminderDraft:
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
            draft = await self._get_owned(session, draft_id, created_by)
            self._expire_if_due(draft, instant)
            expired = draft.status == ReminderDraftStatus.EXPIRED.value
            if not expired and draft.status != ReminderDraftStatus.AWAITING_CONFIRMATION.value:
                raise InvalidReminderDraftTransition(
                    f"reminder draft {draft_id} is not awaiting confirmation"
                )
        if expired:
            raise ReminderDraftExpired(draft_id)
        return draft

    async def confirm(
        self,
        draft_id: str,
        command: ReminderCreate,
        *,
        created_by: str,
        now: datetime | None = None,
    ) -> Reminder:
        instant = _utc(now or self._clock.now())
        draft = await self.get_for_confirmation(
            draft_id,
            created_by=created_by,
            now=instant,
        )
        if command.creator_person_id != draft.created_by:
            raise ReminderDraftPermissionDenied("draft creator does not match reminder creator")

        reminder = await self._reminders.create(command, now=instant)
        async with self._sessions() as session, session.begin():
            current = await self._get_owned(session, draft_id, created_by)
            if current.status != ReminderDraftStatus.AWAITING_CONFIRMATION.value:
                raise InvalidReminderDraftTransition(
                    f"reminder draft {draft_id} is no longer awaiting confirmation"
                )
            validate_reminder_draft_transition(
                ReminderDraftStatus.AWAITING_CONFIRMATION,
                ReminderDraftStatus.CONFIRMED,
            )
            current.status = ReminderDraftStatus.CONFIRMED.value
            current.confirmed_reminder_id = reminder.id
            current.updated_at = instant
        return reminder

    async def cancel(
        self,
        draft_id: str,
        *,
        created_by: str,
        now: datetime | None = None,
    ) -> ReminderDraft:
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
            draft = await self._get_owned(session, draft_id, created_by)
            self._expire_if_due(draft, instant)
            expired = draft.status == ReminderDraftStatus.EXPIRED.value
            if not expired:
                current = ReminderDraftStatus(draft.status)
                validate_reminder_draft_transition(current, ReminderDraftStatus.CANCELLED)
                draft.status = ReminderDraftStatus.CANCELLED.value
                draft.updated_at = instant
        if expired:
            raise ReminderDraftExpired(draft_id)
        return draft

    async def expire_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        if limit < 1:
            raise ValueError("limit must be positive")
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(ReminderDraft)
                    .where(
                        ReminderDraft.status.in_(
                            (
                                ReminderDraftStatus.EDITING.value,
                                ReminderDraftStatus.AWAITING_CONFIRMATION.value,
                            )
                        ),
                        ReminderDraft.expires_at <= instant,
                    )
                    .order_by(ReminderDraft.expires_at, ReminderDraft.id)
                    .limit(limit)
                )
            )
            for draft in rows:
                draft.status = ReminderDraftStatus.EXPIRED.value
                draft.updated_at = instant
        return len(rows)

    async def _get_owned(
        self,
        session: AsyncSession,
        draft_id: str,
        created_by: str,
    ) -> ReminderDraft:
        draft = cast(ReminderDraft | None, await session.get(ReminderDraft, draft_id))
        if draft is None:
            raise ReminderDraftNotFound(draft_id)
        if draft.created_by != created_by:
            raise ReminderDraftPermissionDenied(draft_id)
        return draft

    @staticmethod
    def _expire_if_due(draft: ReminderDraft, now: datetime) -> None:
        if (
            draft.status
            in {
                ReminderDraftStatus.EDITING.value,
                ReminderDraftStatus.AWAITING_CONFIRMATION.value,
            }
            and _utc(draft.expires_at) <= now
        ):
            draft.status = ReminderDraftStatus.EXPIRED.value
            draft.updated_at = now
