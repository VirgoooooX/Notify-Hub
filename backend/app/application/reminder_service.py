from __future__ import annotations

import base64
import builtins
import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from app.domain.reminders import (
    AckPolicy,
    InvalidReminderTransition,
    RecipientStatus,
    ReminderError,
    ReminderSnapshot,
    ReminderStatus,
    ScheduleType,
    action_token_matches,
    hash_action_token,
    next_rrule_occurrence,
    normalize_utc,
    validate_continuous_limits,
    validate_timezone,
)
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Delivery, Event, Notification, Person, WeComIdentity
from app.infrastructure.database.reminder_models import (
    NotificationAction,
    Reminder,
    ReminderRecipient,
)
from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ReminderNotFound(ReminderError):
    pass


class ReminderPermissionDenied(ReminderError):
    pass


@dataclass(frozen=True, slots=True)
class EventAcceptance:
    event_id: str
    accepted: bool
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class ReminderEventDraft:
    source_type: str
    source_id: str
    event_type: str
    event_key: str
    title: str
    content: str
    recipients: tuple[str, ...]
    message_type: str
    require_ack: bool
    ack_policy: str
    payload: dict[str, Any] = field(default_factory=dict)


class ReminderEventEmitter(Protocol):
    async def __call__(self, draft: ReminderEventDraft) -> EventAcceptance: ...


@dataclass(frozen=True, slots=True)
class ReminderCreate:
    creator_person_id: str
    title: str
    content: str
    schedule_type: ScheduleType
    timezone: str
    recipient_ids: tuple[str, ...]
    scheduled_at: datetime | None = None
    recurrence_rule: str | None = None
    require_ack: bool = False
    ack_policy: AckPolicy = AckPolicy.ANY
    repeat_interval_seconds: int | None = None
    max_reminders: int | None = None
    stop_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReminderUpdate:
    title: str | None = None
    content: str | None = None
    schedule_type: ScheduleType | None = None
    timezone: str | None = None
    scheduled_at: datetime | None = None
    recurrence_rule: str | None = None
    recipient_ids: tuple[str, ...] | None = None
    require_ack: bool | None = None
    ack_policy: AckPolicy | None = None
    repeat_interval_seconds: int | None = None
    max_reminders: int | None = None
    stop_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AcknowledgementResult:
    result: str
    reminder_id: str
    completed: bool
    cancelled_deliveries: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ReminderService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        emit_event: ReminderEventEmitter,
        action_token_secret: str,
    ) -> None:
        self._sessions = session_factory
        self._emit_event = emit_event
        self._action_token_secret = action_token_secret.encode()

    def action_token(self, action_id: str) -> str:
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(self._action_token_secret, action_id.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        return f"v1.{action_id}.{signature}"

    async def create(self, command: ReminderCreate, *, now: datetime | None = None) -> Reminder:
        instant = _utc(now or datetime.now(UTC))
        validate_timezone(command.timezone)
        if not command.title.strip():
            raise ReminderError("title is required")
        if not command.recipient_ids:
            raise ReminderError("at least one explicit recipient is required")
        if len(set(command.recipient_ids)) != len(command.recipient_ids):
            raise ReminderError("recipient IDs must be unique")

        if command.schedule_type is ScheduleType.ONCE:
            if command.scheduled_at is None or command.recurrence_rule is not None:
                raise ReminderError("once schedule requires scheduled_at and forbids rrule")
            first_run = normalize_utc(command.scheduled_at)
        else:
            if not command.recurrence_rule:
                raise ReminderError("recurring schedule requires rrule")
            start = normalize_utc(command.scheduled_at or instant)
            recurring_first_run = next_rrule_occurrence(
                command.recurrence_rule,
                timezone=command.timezone,
                after=start - timedelta(microseconds=1),
                dtstart=start,
            )
            if recurring_first_run is None:
                raise ReminderError("recurrence has no future occurrence")
            first_run = recurring_first_run

        interval, maximum, stop_at = validate_continuous_limits(
            require_ack=command.require_ack,
            repeat_interval_seconds=command.repeat_interval_seconds,
            max_reminders=command.max_reminders,
            stop_at=command.stop_at,
            start_at=first_run,
        )
        reminder = Reminder(
            id=new_id("rem"),
            creator_person_id=command.creator_person_id,
            title=command.title.strip(),
            content=command.content.strip(),
            schedule_type=command.schedule_type.value,
            scheduled_at=first_run
            if command.schedule_type is ScheduleType.ONCE
            else command.scheduled_at,
            recurrence_rule=command.recurrence_rule,
            timezone=command.timezone,
            next_run_at=first_run,
            status=ReminderStatus.ACTIVE.value,
            require_ack=command.require_ack,
            ack_policy=command.ack_policy.value,
            repeat_interval_seconds=interval,
            max_reminders=maximum,
            reminder_count=0,
            stop_at=stop_at,
            claimed_by=None,
            claim_expires_at=None,
            created_at=instant,
            updated_at=instant,
        )
        async with self._sessions() as session, session.begin():
            people = set(
                await session.scalars(select(Person.id).where(Person.id.in_(command.recipient_ids)))
            )
            if people != set(command.recipient_ids):
                raise ReminderError("one or more recipients do not exist")
            session.add(reminder)
            session.add_all(
                ReminderRecipient(
                    id=new_id("rrc"),
                    reminder_id=reminder.id,
                    person_id=person_id,
                    status=RecipientStatus.PENDING.value,
                    acknowledged_at=None,
                    last_notified_at=None,
                    notify_count=0,
                )
                for person_id in command.recipient_ids
            )
        return reminder

    async def get(self, reminder_id: str) -> Reminder:
        async with self._sessions() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            return reminder

    async def update(
        self, reminder_id: str, command: ReminderUpdate, *, now: datetime | None = None
    ) -> Reminder:
        instant = _utc(now or datetime.now(UTC))
        async with self._sessions() as session, session.begin():
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            if reminder.status not in {ReminderStatus.ACTIVE.value, ReminderStatus.PAUSED.value}:
                raise InvalidReminderTransition(f"cannot update {reminder.status}")
            if command.title is not None:
                if not command.title.strip():
                    raise ReminderError("title is required")
                reminder.title = command.title.strip()
            if command.content is not None:
                reminder.content = command.content.strip()
            if command.schedule_type is not None:
                timezone = command.timezone or reminder.timezone
                validate_timezone(timezone)
                if command.schedule_type is ScheduleType.ONCE:
                    if command.scheduled_at is None:
                        raise ReminderError("once schedule requires scheduled_at")
                    first_run = normalize_utc(command.scheduled_at)
                    reminder.scheduled_at = first_run
                    reminder.recurrence_rule = None
                else:
                    if not command.recurrence_rule:
                        raise ReminderError("recurring schedule requires rrule")
                    start = normalize_utc(command.scheduled_at or instant)
                    recurring_first_run = next_rrule_occurrence(
                        command.recurrence_rule,
                        timezone=timezone,
                        after=start - timedelta(microseconds=1),
                        dtstart=start,
                    )
                    if recurring_first_run is None:
                        raise ReminderError("recurrence has no future occurrence")
                    first_run = recurring_first_run
                    reminder.scheduled_at = command.scheduled_at
                    reminder.recurrence_rule = command.recurrence_rule
                reminder.schedule_type = command.schedule_type.value
                reminder.timezone = timezone
                reminder.next_run_at = first_run
            require_ack = (
                reminder.require_ack if command.require_ack is None else command.require_ack
            )
            if command.require_ack is not None or any(
                value is not None
                for value in (
                    command.repeat_interval_seconds,
                    command.max_reminders,
                    command.stop_at,
                )
            ):
                interval, maximum, stop_at = validate_continuous_limits(
                    require_ack=require_ack,
                    repeat_interval_seconds=command.repeat_interval_seconds,
                    max_reminders=command.max_reminders,
                    stop_at=command.stop_at,
                    start_at=reminder.next_run_at or instant,
                )
                reminder.require_ack = require_ack
                reminder.repeat_interval_seconds = interval
                reminder.max_reminders = maximum
                reminder.stop_at = stop_at
            if command.ack_policy is not None:
                reminder.ack_policy = command.ack_policy.value
            if command.recipient_ids is not None:
                if reminder.reminder_count:
                    raise ReminderError("recipients cannot change after the first reminder")
                recipients = command.recipient_ids
                if not recipients or len(set(recipients)) != len(recipients):
                    raise ReminderError("recipient IDs must be non-empty and unique")
                people = set(
                    await session.scalars(select(Person.id).where(Person.id.in_(recipients)))
                )
                if people != set(recipients):
                    raise ReminderError("one or more recipients do not exist")
                await session.execute(
                    delete(ReminderRecipient).where(ReminderRecipient.reminder_id == reminder.id)
                )
                session.add_all(
                    ReminderRecipient(
                        id=new_id("rrc"),
                        reminder_id=reminder.id,
                        person_id=person_id,
                        status=RecipientStatus.PENDING.value,
                        acknowledged_at=None,
                        last_notified_at=None,
                        notify_count=0,
                    )
                    for person_id in recipients
                )
            reminder.updated_at = instant
        return reminder

    async def list(
        self, *, offset: int = 0, limit: int = 50, status: str | None = None
    ) -> Sequence[Reminder]:
        if offset < 0 or limit < 1 or limit > 200:
            raise ReminderError("invalid pagination")
        async with self._sessions() as session:
            query = select(Reminder)
            if status == "awaiting_ack":
                query = query.where(
                    Reminder.status == ReminderStatus.ACTIVE.value,
                    Reminder.require_ack.is_(True),
                    Reminder.reminder_count > 0,
                )
            elif status:
                query = query.where(Reminder.status == status)
            rows = await session.scalars(
                query.order_by(Reminder.created_at.desc()).offset(offset).limit(limit)
            )
            return rows.all()

    async def count(self, *, status: str | None = None) -> int:
        async with self._sessions() as session:
            query = select(func.count(Reminder.id))
            if status == "awaiting_ack":
                query = query.where(
                    Reminder.status == ReminderStatus.ACTIVE.value,
                    Reminder.require_ack.is_(True),
                    Reminder.reminder_count > 0,
                )
            elif status:
                query = query.where(Reminder.status == status)
            return int(await session.scalar(query) or 0)

    async def pause(self, reminder_id: str, *, now: datetime | None = None) -> Reminder:
        return await self._transition(reminder_id, "pause", now=now)

    async def resume(self, reminder_id: str, *, now: datetime | None = None) -> Reminder:
        return await self._transition(reminder_id, "activate", now=now)

    async def cancel(self, reminder_id: str, *, now: datetime | None = None) -> Reminder:
        reminder = await self._transition(reminder_id, "cancel", now=now)
        await self._cancel_pending_deliveries(reminder_id)
        return reminder

    async def complete(self, reminder_id: str, *, now: datetime | None = None) -> Reminder:
        reminder = await self._transition(reminder_id, "complete", now=now)
        await self._cancel_pending_deliveries(reminder_id)
        return reminder

    async def snooze(
        self, reminder_id: str, *, until: datetime, actor_person_id: str | None = None
    ) -> Reminder:
        target = normalize_utc(until)
        async with self._sessions() as session, session.begin():
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            if actor_person_id and reminder.creator_person_id != actor_person_id:
                recipients = await session.scalar(
                    select(ReminderRecipient.id).where(
                        ReminderRecipient.reminder_id == reminder_id,
                        ReminderRecipient.person_id == actor_person_id,
                    )
                )
                if recipients is None:
                    raise ReminderPermissionDenied("cannot snooze another person's reminder")
            if reminder.status != ReminderStatus.ACTIVE.value:
                raise InvalidReminderTransition(f"cannot snooze {reminder.status}")
            reminder.next_run_at = target
            reminder.updated_at = datetime.now(UTC)
            return reminder

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 60,
        limit: int = 20,
    ) -> builtins.list[str]:
        instant = _utc(now)
        lease_until = instant + timedelta(seconds=lease_seconds)
        claimed: builtins.list[str] = []
        async with self._sessions() as session, session.begin():
            candidates = await session.scalars(
                select(Reminder.id)
                .where(
                    Reminder.status == ReminderStatus.ACTIVE.value,
                    Reminder.next_run_at <= instant,
                    (Reminder.claim_expires_at.is_(None) | (Reminder.claim_expires_at < instant)),
                )
                .order_by(Reminder.next_run_at)
                .limit(limit)
            )
            for reminder_id in candidates:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(Reminder)
                        .where(
                            Reminder.id == reminder_id,
                            Reminder.status == ReminderStatus.ACTIVE.value,
                            Reminder.next_run_at <= instant,
                            (
                                Reminder.claim_expires_at.is_(None)
                                | (Reminder.claim_expires_at < instant)
                            ),
                        )
                        .values(claimed_by=worker_id, claim_expires_at=lease_until)
                    ),
                )
                if result.rowcount == 1:
                    claimed.append(reminder_id)
        return claimed

    async def trigger_claimed(
        self, reminder_id: str, *, worker_id: str, now: datetime | None = None
    ) -> EventAcceptance | None:
        instant = _utc(now or datetime.now(UTC))
        async with self._sessions() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                return None
            if (
                reminder.status != ReminderStatus.ACTIVE.value
                or reminder.claimed_by != worker_id
                or reminder.next_run_at is None
                or _utc(reminder.next_run_at) > instant
            ):
                return None
            if reminder.stop_at and _utc(reminder.stop_at) <= instant:
                await self._expire_claim(reminder_id, worker_id, instant)
                return None
            recipients = builtins.list(
                await session.scalars(
                    select(ReminderRecipient).where(
                        ReminderRecipient.reminder_id == reminder_id,
                        ReminderRecipient.status == RecipientStatus.PENDING.value,
                    )
                )
            )
            if not recipients:
                await self.complete(reminder_id, now=instant)
                return None
            occurrence = reminder.reminder_count + 1
            action_ids: dict[str, str] = {}
            if reminder.require_ack:
                action_records: list[NotificationAction] = []
                for recipient in recipients:
                    action_id = new_id("act")
                    token = self.action_token(action_id)
                    action_ids[recipient.person_id] = action_id
                    action_records.append(
                        NotificationAction(
                            id=action_id,
                            reminder_id=reminder.id,
                            recipient_id=recipient.id,
                            notification_id=None,
                            action="complete",
                            token_hash=hash_action_token(token),
                            expires_at=_utc(reminder.stop_at)
                            if reminder.stop_at
                            else instant + timedelta(hours=24),
                            consumed_at=None,
                            created_at=instant,
                        )
                    )
                async with self._sessions() as write_session, write_session.begin():
                    write_session.add_all(action_records)

            draft = ReminderEventDraft(
                source_type="reminder",
                source_id=reminder.id,
                event_type="reminder.triggered",
                event_key=f"reminder-{reminder.id}-{occurrence}",
                title=reminder.title,
                content=reminder.content,
                recipients=tuple(item.person_id for item in recipients),
                message_type="template_card" if reminder.require_ack else "text",
                require_ack=reminder.require_ack,
                ack_policy=reminder.ack_policy,
                payload={
                    "reminder_id": reminder.id,
                    "occurrence": occurrence,
                    "action_ids": action_ids,
                    "actions": ["complete"] if reminder.require_ack else [],
                },
            )
        acceptance = await self._emit_event(draft)
        if acceptance.accepted or acceptance.duplicate:
            await self._advance_after_accept(reminder_id, worker_id, instant, recipients)
            await self._cancel_deliveries_if_inactive(reminder_id)
        return acceptance

    async def acknowledge(
        self,
        *,
        action_token: str,
        sender_wecom_userid: str,
        now: datetime | None = None,
    ) -> AcknowledgementResult:
        # The hash lookup avoids storing or scanning plaintext tokens.
        from app.domain.reminders import hash_action_token

        token_hash = hash_action_token(action_token)
        async with self._sessions() as session:
            action_id = await session.scalar(
                select(NotificationAction.id).where(NotificationAction.token_hash == token_hash)
            )
        if action_id is None:
            raise ReminderPermissionDenied("invalid action token")
        return await self.acknowledge_action_id(
            action_id=action_id,
            sender_wecom_userid=sender_wecom_userid,
            now=now,
            expected_token=action_token,
        )

    async def acknowledge_action_id(
        self,
        *,
        action_id: str,
        sender_wecom_userid: str,
        now: datetime | None = None,
        expected_token: str | None = None,
    ) -> AcknowledgementResult:
        instant = _utc(now or datetime.now(UTC))
        async with self._sessions() as session, session.begin():
            action = await session.get(NotificationAction, action_id)
            if action is None or (
                expected_token is not None
                and not action_token_matches(expected_token, action.token_hash)
            ):
                raise ReminderPermissionDenied("invalid action token")
            identity = await session.scalar(
                select(WeComIdentity).where(
                    WeComIdentity.user_id == sender_wecom_userid,
                    WeComIdentity.active.is_(True),
                )
            )
            recipient = await session.get(ReminderRecipient, action.recipient_id)
            reminder = await session.get(Reminder, action.reminder_id)
            if identity is None or recipient is None or reminder is None:
                raise ReminderPermissionDenied("sender is not a reminder recipient")
            if recipient.person_id != identity.person_id:
                raise ReminderPermissionDenied("sender is not authorized for this action")
            if reminder.status == ReminderStatus.COMPLETED.value:
                return AcknowledgementResult("already_completed", reminder.id, True, 0)
            if reminder.status != ReminderStatus.ACTIVE.value:
                return AcknowledgementResult("not_active", reminder.id, False, 0)
            if _utc(action.expires_at) < instant:
                raise ReminderPermissionDenied("action token has expired")
            if recipient.status == RecipientStatus.ACKNOWLEDGED.value:
                return AcknowledgementResult("already_acknowledged", reminder.id, False, 0)
            recipient.status = RecipientStatus.ACKNOWLEDGED.value
            recipient.acknowledged_at = instant
            action.consumed_at = instant
            pending = await session.scalar(
                select(ReminderRecipient.id).where(
                    ReminderRecipient.reminder_id == reminder.id,
                    ReminderRecipient.status == RecipientStatus.PENDING.value,
                )
            )
            completed = reminder.ack_policy == AckPolicy.ANY.value or pending is None
            if completed:
                reminder.status = ReminderStatus.COMPLETED.value
                reminder.next_run_at = None
                reminder.claimed_by = None
                reminder.claim_expires_at = None
                reminder.updated_at = instant
        cancelled = await self._cancel_pending_deliveries(action.reminder_id) if completed else 0
        return AcknowledgementResult(
            "completed" if completed else "acknowledged", action.reminder_id, completed, cancelled
        )

    async def _transition(
        self, reminder_id: str, operation: str, *, now: datetime | None
    ) -> Reminder:
        instant = _utc(now or datetime.now(UTC))
        async with self._sessions() as session, session.begin():
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            snapshot = ReminderSnapshot(
                status=ReminderStatus(reminder.status),
                require_ack=reminder.require_ack,
                next_run_at=reminder.next_run_at,
                stop_at=reminder.stop_at,
                reminder_count=reminder.reminder_count,
                max_reminders=reminder.max_reminders,
            )
            changed = getattr(snapshot, operation)()
            reminder.status = changed.status.value
            reminder.next_run_at = changed.next_run_at
            reminder.updated_at = instant
            reminder.claimed_by = None
            reminder.claim_expires_at = None
            return reminder

    async def _advance_after_accept(
        self,
        reminder_id: str,
        worker_id: str,
        now: datetime,
        recipients: Sequence[ReminderRecipient],
    ) -> None:
        async with self._sessions() as session, session.begin():
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None or reminder.claimed_by != worker_id:
                return
            # Recheck after event acceptance: a concurrent acknowledgement may have completed it.
            if reminder.status != ReminderStatus.ACTIVE.value:
                reminder.claimed_by = None
                reminder.claim_expires_at = None
                return
            reminder.reminder_count += 1
            reminder.updated_at = now
            for prior in recipients:
                current = await session.get(ReminderRecipient, prior.id)
                if current and current.status == RecipientStatus.PENDING.value:
                    current.last_notified_at = now
                    current.notify_count += 1
            if reminder.require_ack:
                exhausted = bool(
                    reminder.max_reminders and reminder.reminder_count >= reminder.max_reminders
                )
                next_run = now + timedelta(seconds=reminder.repeat_interval_seconds or 300)
                if exhausted or (reminder.stop_at and next_run >= _utc(reminder.stop_at)):
                    reminder.status = ReminderStatus.EXPIRED.value
                    reminder.next_run_at = None
                    await session.execute(
                        update(ReminderRecipient)
                        .where(
                            ReminderRecipient.reminder_id == reminder.id,
                            ReminderRecipient.status == RecipientStatus.PENDING.value,
                        )
                        .values(status=RecipientStatus.EXPIRED.value)
                    )
                else:
                    reminder.next_run_at = next_run
            elif reminder.schedule_type == ScheduleType.ONCE.value:
                reminder.status = ReminderStatus.COMPLETED.value
                reminder.next_run_at = None
            else:
                assert reminder.recurrence_rule is not None
                recurring_next_run = next_rrule_occurrence(
                    reminder.recurrence_rule,
                    timezone=reminder.timezone,
                    after=now,
                    dtstart=reminder.scheduled_at or reminder.created_at,
                )
                reminder.next_run_at = recurring_next_run
                if recurring_next_run is None:
                    reminder.status = ReminderStatus.COMPLETED.value
            reminder.claimed_by = None
            reminder.claim_expires_at = None

    async def _expire_claim(self, reminder_id: str, worker_id: str, now: datetime) -> None:
        async with self._sessions() as session, session.begin():
            reminder = await session.get(Reminder, reminder_id)
            if reminder and reminder.claimed_by == worker_id:
                reminder.status = ReminderStatus.EXPIRED.value
                reminder.next_run_at = None
                reminder.claimed_by = None
                reminder.claim_expires_at = None
                reminder.updated_at = now
                await session.execute(
                    update(ReminderRecipient)
                    .where(
                        ReminderRecipient.reminder_id == reminder.id,
                        ReminderRecipient.status == RecipientStatus.PENDING.value,
                    )
                    .values(status=RecipientStatus.EXPIRED.value)
                )

    async def _cancel_pending_deliveries(self, reminder_id: str) -> int:
        async with self._sessions() as session, session.begin():
            reminder_events: Select[tuple[str]] = select(Event.id).where(
                Event.source_type == "reminder", Event.source_id == reminder_id
            )
            notification_ids = select(Notification.id).where(
                Notification.event_id.in_(reminder_events)
            )
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(Delivery)
                    .where(
                        Delivery.notification_id.in_(notification_ids),
                        Delivery.status.in_(("pending", "retry_wait")),
                    )
                    .values(
                        status="cancelled",
                        last_error_code="reminder_acknowledged",
                        last_error_message="reminder stopped before delivery",
                        updated_at=datetime.now(UTC),
                    )
                ),
            )
            return int(result.rowcount or 0)

    async def _cancel_deliveries_if_inactive(self, reminder_id: str) -> int:
        async with self._sessions() as session:
            status = await session.scalar(select(Reminder.status).where(Reminder.id == reminder_id))
        if status == ReminderStatus.ACTIVE.value:
            return 0
        return await self._cancel_pending_deliveries(reminder_id)
