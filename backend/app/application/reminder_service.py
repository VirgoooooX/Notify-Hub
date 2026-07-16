from __future__ import annotations

import base64
import builtins
import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from app.application.audit import add_audit
from app.application.reminder_delivery_service import ReminderDeliveryService
from app.application.reminder_scheduling import (
    schedule_from_reminder as _schedule_from_reminder,
)
from app.application.reminder_scheduling import (
    structured_schedule as _structured_schedule,
)
from app.application.reminder_scheduling import (
    utc_datetime as _utc,
)
from app.domain.clock import Clock, SystemClock
from app.domain.reminder_schedules import (
    MisfirePolicy,
    ScheduleValidationError,
    next_occurrence,
    resolve_due,
    validate_minimum_frequency,
)
from app.domain.reminders import (
    AckPolicy,
    InvalidReminderTransition,
    RecipientStatus,
    ReminderError,
    ReminderSnapshot,
    ReminderStatus,
    ScheduleType,
    action_token_matches,
    next_rrule_occurrence,
    normalize_utc,
    validate_continuous_limits,
    validate_timezone,
)
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Delivery, Notification, Person, WeComIdentity
from app.infrastructure.database.reminder_models import (
    IncomingMessage,
    NotificationAction,
    Reminder,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
    ReminderRecipient,
)
from sqlalchemy import delete, exists, func, or_, select, update
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
    broadcast: bool = False
    reminder_occurrence_id: str | None = None
    url: str | None = None
    media_asset_id: str | None = None
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
    broadcast: bool = False
    notify_on_all_completed: bool = False
    scheduled_at: datetime | None = None
    recurrence_rule: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE
    require_ack: bool = False
    ack_policy: AckPolicy = AckPolicy.ANY
    repeat_interval_seconds: int | None = None
    max_reminders: int | None = None
    stop_at: datetime | None = None
    content_type: str = "text"
    media_asset_id: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ReminderUpdate:
    title: str | None = None
    content: str | None = None
    schedule_type: ScheduleType | None = None
    timezone: str | None = None
    scheduled_at: datetime | None = None
    recurrence_rule: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    misfire_policy: MisfirePolicy | None = None
    recipient_ids: tuple[str, ...] | None = None
    require_ack: bool | None = None
    ack_policy: AckPolicy | None = None
    repeat_interval_seconds: int | None = None
    max_reminders: int | None = None
    stop_at: datetime | None = None
    content_type: str | None = None
    media_asset_id: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class AcknowledgementResult:
    result: str
    reminder_id: str
    completed: bool
    cancelled_deliveries: int


@dataclass(frozen=True, slots=True)
class InteractiveOccurrenceResult:
    code: str
    title: str
    reminder_id: str
    occurrence_id: str


INTERACTIVE_REMINDER_HINT = (
    "⏳ 本提醒将在完成前持续发送\n📍 完成入口：底部【快捷操作】→【完成本次】"
)
BROADCAST_INTERACTIVE_REMINDER_HINT = (
    "⏳ 未完成成员将继续收到提醒\n📍 完成入口：底部【快捷操作】→【完成本次】"
)
ALL_COMPLETED_NOTIFICATION_HINT = "全部成员完成后，将发送全员完成通知"


def _broadcast_interactive_hint(notify_on_all_completed: bool) -> str:
    if notify_on_all_completed:
        return f"{BROADCAST_INTERACTIVE_REMINDER_HINT}\n{ALL_COMPLETED_NOTIFICATION_HINT}"
    return BROADCAST_INTERACTIVE_REMINDER_HINT


class ReminderService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        emit_event: ReminderEventEmitter,
        action_token_secret: str,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = session_factory
        self._emit_event = emit_event
        self._action_token_secret = action_token_secret.encode()
        self._clock = clock or SystemClock()
        self._deliveries = ReminderDeliveryService(self._sessions, self._clock.now)

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
        instant = _utc(now or self._clock.now())
        validate_timezone(command.timezone)
        if not command.title.strip():
            raise ReminderError("title is required")
        if not command.recipient_ids:
            raise ReminderError("at least one explicit recipient is required")
        if len(set(command.recipient_ids)) != len(command.recipient_ids):
            raise ReminderError("recipient IDs must be unique")

        schedule_config: dict[str, Any] = {}
        start_at = normalize_utc(command.start_at) if command.start_at else None
        end_at = normalize_utc(command.end_at) if command.end_at else None
        if start_at and end_at and start_at > end_at:
            raise ReminderError("start_at must not be after end_at")
        if command.schedule_type in {
            ScheduleType.ONCE,
            ScheduleType.INTERVAL,
            ScheduleType.CRON,
        }:
            schedule = _structured_schedule(
                schedule_type=command.schedule_type,
                timezone=command.timezone,
                scheduled_at=command.scheduled_at,
                interval_seconds=command.interval_seconds,
                cron_expression=command.cron_expression,
                start_at=start_at,
            )
            try:
                validate_minimum_frequency(schedule, after=instant, minimum_seconds=300)
                first_run = next_occurrence(
                    schedule,
                    after=instant,
                    inclusive=True,
                    start_at=start_at,
                    end_at=end_at,
                )
            except ScheduleValidationError as exc:
                raise ReminderError(str(exc)) from exc
            if first_run is None:
                raise ReminderError("schedule has no future occurrence")
            if command.schedule_type is ScheduleType.INTERVAL:
                schedule_config = {"seconds": command.interval_seconds}
            elif command.schedule_type is ScheduleType.CRON:
                schedule_config = {"expression": command.cron_expression}
        elif command.schedule_type is ScheduleType.RECURRING:
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
        else:
            raise ReminderError("unsupported schedule type")
        if first_run < instant:
            raise ReminderError("first reminder occurrence must not be in the past")

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
            content_type=command.content_type,
            media_asset_id=command.media_asset_id,
            url=command.url,
            schedule_type=command.schedule_type.value,
            scheduled_at=first_run,
            recurrence_rule=command.recurrence_rule,
            schedule_config=schedule_config,
            timezone=command.timezone,
            start_at=start_at,
            end_at=end_at,
            misfire_policy=command.misfire_policy.value,
            next_run_at=first_run,
            status=ReminderStatus.ACTIVE.value,
            broadcast=command.broadcast,
            notify_on_all_completed=command.notify_on_all_completed,
            require_ack=command.require_ack,
            ack_policy=command.ack_policy.value,
            repeat_interval_seconds=interval,
            max_reminders=maximum,
            reminder_count=0,
            stop_at=stop_at,
            escalation_stop_after_seconds=(
                int((stop_at - first_run).total_seconds()) if stop_at else None
            ),
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
            if command.content_type not in {"text", "image", "article"}:
                raise ReminderError("unsupported reminder content type")
            if command.url and not command.url.startswith(("https://", "http://")):
                raise ReminderError("reminder URL must use HTTP or HTTPS")
            if command.content_type in {"image", "article"} and not command.media_asset_id:
                raise ReminderError("image and article reminders require a media asset")
            if command.media_asset_id:
                from app.infrastructure.database.media_models import MediaAsset

                asset = await session.get(MediaAsset, command.media_asset_id)
                if asset is None:
                    raise ReminderError("media asset does not exist")
                if asset.kind != "image":
                    raise ReminderError("reminder media asset must be an image")
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
        instant = _utc(now or self._clock.now())
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
            if command.content_type is not None:
                if command.content_type not in {"text", "image", "article"}:
                    raise ReminderError("unsupported reminder content type")
                reminder.content_type = command.content_type
            if command.media_asset_id is not None:
                if command.media_asset_id:
                    from app.infrastructure.database.media_models import MediaAsset

                    asset = await session.get(MediaAsset, command.media_asset_id)
                    if asset is None:
                        raise ReminderError("media asset does not exist")
                    if asset.kind != "image":
                        raise ReminderError("reminder media asset must be an image")
                reminder.media_asset_id = command.media_asset_id or None
            if command.url is not None:
                if command.url and not command.url.startswith(("https://", "http://")):
                    raise ReminderError("reminder URL must use HTTP or HTTPS")
                reminder.url = command.url or None
            if reminder.content_type in {"image", "article"} and not reminder.media_asset_id:
                raise ReminderError("image and article reminders require a media asset")
            if command.schedule_type is not None:
                timezone = command.timezone or reminder.timezone
                validate_timezone(timezone)
                start_at = normalize_utc(command.start_at) if command.start_at else None
                end_at = normalize_utc(command.end_at) if command.end_at else None
                if start_at and end_at and start_at > end_at:
                    raise ReminderError("start_at must not be after end_at")
                if command.schedule_type in {
                    ScheduleType.ONCE,
                    ScheduleType.INTERVAL,
                    ScheduleType.CRON,
                }:
                    schedule = _structured_schedule(
                        schedule_type=command.schedule_type,
                        timezone=timezone,
                        scheduled_at=command.scheduled_at,
                        interval_seconds=command.interval_seconds,
                        cron_expression=command.cron_expression,
                        start_at=start_at,
                    )
                    try:
                        validate_minimum_frequency(schedule, after=instant, minimum_seconds=300)
                        first_run = next_occurrence(
                            schedule,
                            after=instant,
                            inclusive=True,
                            start_at=start_at,
                            end_at=end_at,
                        )
                    except ScheduleValidationError as exc:
                        raise ReminderError(str(exc)) from exc
                    if first_run is None:
                        raise ReminderError("schedule has no future occurrence")
                    reminder.recurrence_rule = None
                    if command.schedule_type is ScheduleType.INTERVAL:
                        reminder.schedule_config = {"seconds": command.interval_seconds}
                    elif command.schedule_type is ScheduleType.CRON:
                        reminder.schedule_config = {"expression": command.cron_expression}
                    else:
                        reminder.schedule_config = {}
                elif command.schedule_type is ScheduleType.RECURRING:
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
                    reminder.recurrence_rule = command.recurrence_rule
                    reminder.schedule_config = {}
                else:
                    raise ReminderError("unsupported schedule type")
                reminder.scheduled_at = first_run
                reminder.schedule_type = command.schedule_type.value
                reminder.timezone = timezone
                reminder.start_at = start_at
                reminder.end_at = end_at
                reminder.misfire_policy = (
                    command.misfire_policy or MisfirePolicy(reminder.misfire_policy)
                ).value
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
                reminder.escalation_stop_after_seconds = (
                    int((stop_at - (reminder.next_run_at or instant)).total_seconds())
                    if stop_at
                    else None
                )
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
            if reminder.content_type == "image" and not reminder.media_asset_id:
                raise ReminderError("image reminders require a media asset")
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
        await self._cancel_active_occurrences(reminder_id, now=now)
        await self._cancel_pending_deliveries(reminder_id)
        return reminder

    async def complete(self, reminder_id: str, *, now: datetime | None = None) -> Reminder:
        reminder = await self._transition(reminder_id, "complete", now=now)
        await self._cancel_active_occurrences(reminder_id, now=now)
        await self._cancel_pending_deliveries(reminder_id)
        return reminder

    async def delete(self, reminder_id: str, *, now: datetime | None = None) -> None:
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            current_status = reminder.status

        if current_status in {ReminderStatus.ACTIVE.value, ReminderStatus.PAUSED.value}:
            await self.cancel(reminder_id, now=instant)
        else:
            await self._cancel_pending_deliveries(reminder_id)

        async with self._sessions() as session, session.begin():
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            await session.delete(reminder)

    async def _cancel_active_occurrences(
        self, reminder_id: str, *, now: datetime | None = None
    ) -> None:
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
            occurrence_ids = select(ReminderOccurrence.id).where(
                ReminderOccurrence.reminder_id == reminder_id,
                ReminderOccurrence.status.in_(("scheduled", "active")),
            )
            await session.execute(
                update(ReminderOccurrenceRecipient)
                .where(
                    ReminderOccurrenceRecipient.occurrence_id.in_(occurrence_ids),
                    ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                )
                .values(
                    status=RecipientStatus.CANCELLED.value,
                    next_notify_at=None,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=instant,
                )
            )
            await session.execute(
                update(ReminderOccurrence)
                .where(
                    ReminderOccurrence.reminder_id == reminder_id,
                    ReminderOccurrence.status.in_(("scheduled", "active")),
                )
                .values(status="cancelled", updated_at=instant)
            )

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
            reminder.updated_at = self._clock.now()
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
        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
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
            recipients = list(
                await session.scalars(
                    select(ReminderRecipient).where(
                        ReminderRecipient.reminder_id == reminder_id,
                    )
                )
            )
            if not recipients:
                reminder.status = ReminderStatus.COMPLETED.value
                reminder.next_run_at = None
                reminder.claimed_by = None
                reminder.claim_expires_at = None
                reminder.updated_at = instant
                return None

            scheduled_time = _utc(reminder.next_run_at)
            structured_schedule = _schedule_from_reminder(reminder)
            structured_next_run: datetime | None = None
            if structured_schedule is not None:
                try:
                    resolution = resolve_due(
                        structured_schedule,
                        scheduled_for=scheduled_time,
                        now=instant,
                        policy=MisfirePolicy(reminder.misfire_policy),
                        start_at=_utc(reminder.start_at) if reminder.start_at else None,
                        end_at=_utc(reminder.end_at) if reminder.end_at else None,
                    )
                except ScheduleValidationError as exc:
                    raise ReminderError(str(exc)) from exc
                structured_next_run = resolution.next_trigger_at
                if resolution.occurrence_at is None:
                    reminder.next_run_at = structured_next_run
                    if structured_next_run is None:
                        reminder.status = ReminderStatus.EXPIRED.value
                    reminder.claimed_by = None
                    reminder.claim_expires_at = None
                    reminder.updated_at = instant
                    return None
                scheduled_time = resolution.occurrence_at
            occurrence_key = f"reminder:{reminder.id}:{scheduled_time.isoformat()}"

            existing_occurrence = await session.scalar(
                select(ReminderOccurrence).where(
                    ReminderOccurrence.reminder_id == reminder.id,
                    ReminderOccurrence.occurrence_key == occurrence_key,
                )
            )
            if existing_occurrence is not None:
                reminder.claimed_by = None
                reminder.claim_expires_at = None
                return None

            escalation_deadline: datetime | None = None
            if reminder.require_ack:
                duration = timedelta(seconds=reminder.escalation_stop_after_seconds or 86_400)
                escalation_deadline = scheduled_time + duration

            occurrence = ReminderOccurrence(
                id=new_id("roc"),
                reminder_id=reminder.id,
                occurrence_key=occurrence_key,
                scheduled_for=scheduled_time,
                triggered_at=instant,
                status="active",
                broadcast_snapshot=reminder.broadcast,
                notify_on_all_completed_snapshot=reminder.notify_on_all_completed,
                broadcast_sent_at=None,
                broadcast_completion_announced_at=None,
                broadcast_claimed_by=None,
                broadcast_claim_expires_at=None,
                title_snapshot=reminder.title,
                content_snapshot=reminder.content,
                content_type_snapshot=reminder.content_type,
                media_asset_id_snapshot=reminder.media_asset_id,
                url=reminder.url,
                ack_policy_snapshot=reminder.ack_policy,
                repeat_interval_seconds_snapshot=reminder.repeat_interval_seconds,
                max_reminders_snapshot=reminder.max_reminders,
                stop_at_snapshot=escalation_deadline,
                expires_at=escalation_deadline,
                created_at=instant,
                updated_at=instant,
            )
            session.add(occurrence)

            occurrence_recipients = [
                ReminderOccurrenceRecipient(
                    id=new_id("ror"),
                    occurrence_id=occurrence.id,
                    person_id=recipient.person_id,
                    status="pending",
                    notify_count=0,
                    next_notify_at=instant,
                    last_notified_at=None,
                    acknowledged_at=None,
                    acknowledged_by=None,
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=instant,
                    updated_at=instant,
                )
                for recipient in recipients
            ]
            session.add_all(occurrence_recipients)
            await self._advance_reminder_schedule(
                reminder,
                instant,
                session,
                structured_next_run=structured_next_run,
            )
        return None

    async def _advance_reminder_schedule(
        self,
        reminder: Reminder,
        now: datetime,
        session: AsyncSession,
        *,
        structured_next_run: datetime | None = None,
    ) -> None:
        reminder.reminder_count += 1
        reminder.updated_at = now
        if reminder.schedule_type == ScheduleType.ONCE.value:
            if not reminder.require_ack:
                reminder.status = ReminderStatus.COMPLETED.value
            reminder.next_run_at = None
        elif reminder.schedule_type == ScheduleType.RECURRING.value:
            assert reminder.recurrence_rule is not None
            recurring_next_run = next_rrule_occurrence(
                reminder.recurrence_rule,
                timezone=reminder.timezone,
                after=_utc(reminder.next_run_at or now),
                dtstart=_utc(reminder.scheduled_at or reminder.created_at),
            )
            reminder.next_run_at = recurring_next_run
            if recurring_next_run is None and not reminder.require_ack:
                reminder.status = ReminderStatus.COMPLETED.value
        else:
            reminder.next_run_at = structured_next_run
            if structured_next_run is None:
                reminder.status = ReminderStatus.COMPLETED.value
        reminder.claimed_by = None
        reminder.claim_expires_at = None

    async def claim_due_broadcasts(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 60,
        limit: int = 20,
    ) -> builtins.list[str]:
        """Claim initial @all sends and all-complete announcements."""
        instant = _utc(now)
        lease_until = instant + timedelta(seconds=lease_seconds)
        async with self._sessions() as session, session.begin():
            candidates = list(
                await session.scalars(
                    select(ReminderOccurrence.id)
                    .where(
                        ReminderOccurrence.broadcast_snapshot.is_(True),
                        or_(
                            (
                                (ReminderOccurrence.status == "active")
                                & ReminderOccurrence.broadcast_sent_at.is_(None)
                            ),
                            (
                                (ReminderOccurrence.status == "acknowledged")
                                & ReminderOccurrence.broadcast_sent_at.is_not(None)
                                & ReminderOccurrence.repeat_interval_seconds_snapshot.is_not(None)
                                & ReminderOccurrence.notify_on_all_completed_snapshot.is_(True)
                                & ReminderOccurrence.broadcast_completion_announced_at.is_(None)
                                & ~exists(
                                    select(ReminderOccurrenceRecipient.id).where(
                                        ReminderOccurrenceRecipient.occurrence_id
                                        == ReminderOccurrence.id,
                                        ReminderOccurrenceRecipient.status
                                        != RecipientStatus.ACKNOWLEDGED.value,
                                    )
                                )
                            ),
                        ),
                        or_(
                            ReminderOccurrence.broadcast_claim_expires_at.is_(None),
                            ReminderOccurrence.broadcast_claim_expires_at < instant,
                        ),
                    )
                    .order_by(ReminderOccurrence.created_at)
                    .limit(limit)
                )
            )
            claimed: builtins.list[str] = []
            for occurrence_id in candidates:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(ReminderOccurrence)
                        .where(
                            ReminderOccurrence.id == occurrence_id,
                            or_(
                                ReminderOccurrence.broadcast_claim_expires_at.is_(None),
                                ReminderOccurrence.broadcast_claim_expires_at < instant,
                            ),
                        )
                        .values(
                            broadcast_claimed_by=worker_id,
                            broadcast_claim_expires_at=lease_until,
                        )
                    ),
                )
                if result.rowcount == 1:
                    claimed.append(occurrence_id)
            return claimed

    async def notify_broadcast(
        self, occurrence_id: str, *, worker_id: str, now: datetime
    ) -> EventAcceptance | None:
        instant = _utc(now)
        async with self._sessions() as session:
            occurrence = await session.get(ReminderOccurrence, occurrence_id)
            if occurrence is None or occurrence.broadcast_claimed_by != worker_id:
                return None
            reminder = await session.get(Reminder, occurrence.reminder_id)
            if reminder is None:
                return None
            completion = (
                occurrence.status == "acknowledged"
                and occurrence.broadcast_sent_at is not None
                and occurrence.notify_on_all_completed_snapshot
                and occurrence.broadcast_completion_announced_at is None
            )
            if completion:
                draft = ReminderEventDraft(
                    source_type="reminder",
                    source_id=reminder.id,
                    event_type="reminder.broadcast_all_completed",
                    event_key=f"reminder:{occurrence.id}:broadcast-all-completed",
                    title=f"✅ 全员已完成：{occurrence.title_snapshot}",
                    content="",
                    recipients=("@all",),
                    message_type="text",
                    require_ack=False,
                    ack_policy=occurrence.ack_policy_snapshot,
                    broadcast=True,
                    reminder_occurrence_id=occurrence.id,
                    payload={
                        "reminder_id": reminder.id,
                        "occurrence_id": occurrence.id,
                        "broadcast_completion": True,
                    },
                )
            elif occurrence.status == "active" and occurrence.broadcast_sent_at is None:
                interactive = occurrence.repeat_interval_seconds_snapshot is not None
                content = occurrence.content_snapshot
                message_type = occurrence.content_type_snapshot
                if (
                    message_type == "article"
                    and not occurrence.url
                    and not occurrence.media_asset_id_snapshot
                ):
                    message_type = "text"
                if interactive:
                    hint = _broadcast_interactive_hint(occurrence.notify_on_all_completed_snapshot)
                    content = f"{content}\n\n{hint}" if content else hint
                draft = ReminderEventDraft(
                    source_type="reminder",
                    source_id=reminder.id,
                    event_type="reminder.broadcast_triggered",
                    event_key=f"reminder:{occurrence.id}:broadcast-initial",
                    title=(
                        f"📣 全员持续提醒｜{occurrence.title_snapshot}"
                        if interactive
                        else occurrence.title_snapshot
                    ),
                    content=content,
                    recipients=("@all",),
                    message_type=message_type,
                    require_ack=interactive,
                    ack_policy=occurrence.ack_policy_snapshot,
                    broadcast=True,
                    reminder_occurrence_id=occurrence.id,
                    url=occurrence.url,
                    media_asset_id=occurrence.media_asset_id_snapshot,
                    payload={
                        "reminder_id": reminder.id,
                        "occurrence_id": occurrence.id,
                        "interactive_reminder": interactive,
                        "broadcast_reminder": True,
                    },
                )
            else:
                return None

        try:
            acceptance = await self._emit_event(draft)
        except Exception:
            await self._release_broadcast_claim(occurrence_id, worker_id)
            raise
        if not (acceptance.accepted or acceptance.duplicate):
            await self._release_broadcast_claim(occurrence_id, worker_id)
            return acceptance

        async with self._sessions() as session, session.begin():
            occurrence = await session.get(ReminderOccurrence, occurrence_id)
            if occurrence is None or occurrence.broadcast_claimed_by != worker_id:
                return acceptance
            if completion:
                occurrence.broadcast_completion_announced_at = instant
            else:
                occurrence.broadcast_sent_at = instant
                interactive = occurrence.repeat_interval_seconds_snapshot is not None
                recipients = list(
                    await session.scalars(
                        select(ReminderOccurrenceRecipient).where(
                            ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                            ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                        )
                    )
                )
                for recipient in recipients:
                    recipient.notify_count += 1
                    recipient.last_notified_at = instant
                    recipient.claimed_by = None
                    recipient.claim_expires_at = None
                    recipient.updated_at = instant
                    if interactive:
                        recipient.next_notify_at = instant + timedelta(
                            seconds=occurrence.repeat_interval_seconds_snapshot or 300
                        )
                    else:
                        recipient.status = RecipientStatus.ACKNOWLEDGED.value
                        recipient.acknowledged_at = instant
                        recipient.next_notify_at = None
                if not interactive:
                    occurrence.status = "acknowledged"
                    occurrence.completed_at = instant
            occurrence.broadcast_claimed_by = None
            occurrence.broadcast_claim_expires_at = None
            occurrence.updated_at = instant
        return acceptance

    async def _release_broadcast_claim(self, occurrence_id: str, worker_id: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(ReminderOccurrence)
                .where(
                    ReminderOccurrence.id == occurrence_id,
                    ReminderOccurrence.broadcast_claimed_by == worker_id,
                )
                .values(broadcast_claimed_by=None, broadcast_claim_expires_at=None)
            )

    async def claim_due_recipients(
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
                select(ReminderOccurrenceRecipient.id)
                .join(
                    ReminderOccurrence,
                    ReminderOccurrence.id == ReminderOccurrenceRecipient.occurrence_id,
                )
                .join(Reminder, Reminder.id == ReminderOccurrence.reminder_id)
                .where(
                    ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                    ReminderOccurrenceRecipient.next_notify_at <= instant,
                    (
                        ReminderOccurrenceRecipient.claim_expires_at.is_(None)
                        | (ReminderOccurrenceRecipient.claim_expires_at < instant)
                    ),
                    ReminderOccurrence.status == "active",
                    or_(
                        ReminderOccurrence.broadcast_snapshot.is_(False),
                        ReminderOccurrence.broadcast_sent_at.is_not(None),
                    ),
                    (
                        (Reminder.status == ReminderStatus.ACTIVE.value)
                        | (
                            (Reminder.status == ReminderStatus.COMPLETED.value)
                            & ReminderOccurrence.repeat_interval_seconds_snapshot.is_(None)
                            & (ReminderOccurrenceRecipient.notify_count == 0)
                        )
                    ),
                )
                .order_by(ReminderOccurrenceRecipient.next_notify_at)
                .limit(limit)
            )
            for recipient_id in candidates:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(ReminderOccurrenceRecipient)
                        .where(
                            ReminderOccurrenceRecipient.id == recipient_id,
                            ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                            ReminderOccurrenceRecipient.next_notify_at <= instant,
                            (
                                ReminderOccurrenceRecipient.claim_expires_at.is_(None)
                                | (ReminderOccurrenceRecipient.claim_expires_at < instant)
                            ),
                        )
                        .values(claimed_by=worker_id, claim_expires_at=lease_until)
                    ),
                )
                if result.rowcount == 1:
                    claimed.append(recipient_id)
        return claimed

    async def notify_recipient(
        self, recipient_id: str, *, worker_id: str, now: datetime
    ) -> EventAcceptance | None:
        instant = _utc(now)
        async with self._sessions() as session, session.begin():
            recipient = await session.get(ReminderOccurrenceRecipient, recipient_id)
            if (
                recipient is None
                or recipient.status != RecipientStatus.PENDING.value
                or recipient.claimed_by != worker_id
            ):
                return None
            occurrence = await session.get(ReminderOccurrence, recipient.occurrence_id)
            if occurrence is None or occurrence.status != "active":
                if occurrence:
                    recipient.status = occurrence.status
                    recipient.next_notify_at = None
                recipient.claimed_by = None
                recipient.claim_expires_at = None
                return None

            max_notifications = occurrence.max_reminders_snapshot or 12
            repeat_interval = occurrence.repeat_interval_seconds_snapshot or 300
            stop_at = occurrence.stop_at_snapshot

            exhausted = recipient.notify_count >= max_notifications
            timed_out = stop_at and instant >= _utc(stop_at)

            if exhausted or timed_out:
                recipient.status = RecipientStatus.EXPIRED.value
                recipient.next_notify_at = None
                recipient.claimed_by = None
                recipient.claim_expires_at = None
                await self._recheck_occurrence_status(occurrence, session, instant)
                return None

            interactive = occurrence.repeat_interval_seconds_snapshot is not None
            message_type = occurrence.content_type_snapshot
            if (
                message_type == "article"
                and not occurrence.url
                and not occurrence.media_asset_id_snapshot
            ):
                message_type = "text"
            content = occurrence.content_snapshot
            if interactive:
                hint = (
                    _broadcast_interactive_hint(occurrence.notify_on_all_completed_snapshot)
                    if occurrence.broadcast_snapshot
                    else INTERACTIVE_REMINDER_HINT
                )
                content = f"{content}\n\n{hint}" if content else hint

            draft = ReminderEventDraft(
                source_type="reminder",
                source_id=occurrence.reminder_id,
                event_type="reminder.triggered",
                event_key=(
                    f"reminder:{occurrence.id}:{recipient.person_id}:{recipient.notify_count + 1}"
                ),
                title=(
                    f"📣 全员持续提醒｜{occurrence.title_snapshot}"
                    if interactive and occurrence.broadcast_snapshot
                    else f"🔁 持续提醒｜{occurrence.title_snapshot}"
                    if interactive
                    else occurrence.title_snapshot
                ),
                content=content,
                recipients=(recipient.person_id,),
                message_type=message_type,
                require_ack=interactive,
                ack_policy=occurrence.ack_policy_snapshot,
                reminder_occurrence_id=occurrence.id,
                url=occurrence.url,
                media_asset_id=occurrence.media_asset_id_snapshot,
                payload={
                    "reminder_id": occurrence.reminder_id,
                    "occurrence_id": occurrence.id,
                    "occurrence_recipient_id": recipient.id,
                    "occurrence": recipient.notify_count + 1,
                    "interactive_reminder": interactive,
                },
            )

        try:
            acceptance = await self._emit_event(draft)
        except Exception:
            await self._release_recipient_claim(recipient_id, worker_id)
            raise
        if not (acceptance.accepted or acceptance.duplicate):
            await self._release_recipient_claim(recipient_id, worker_id)
            return acceptance

        cancel_after_accept = False
        async with self._sessions() as write_session, write_session.begin():
            current = await write_session.get(ReminderOccurrenceRecipient, recipient_id)
            current_occurrence = await write_session.get(ReminderOccurrence, occurrence.id)
            notification = await write_session.scalar(
                select(Notification).where(Notification.event_id == acceptance.event_id)
            )
            if notification is not None:
                notification.reminder_occurrence_id = occurrence.id
            if (
                current is not None
                and current_occurrence is not None
                and current.status == RecipientStatus.PENDING.value
                and current.claimed_by == worker_id
            ):
                current.notify_count += 1
                current.last_notified_at = instant
                current.claimed_by = None
                current.claim_expires_at = None
                if current_occurrence.repeat_interval_seconds_snapshot is None:
                    current.status = RecipientStatus.ACKNOWLEDGED.value
                    current.next_notify_at = None
                    current_occurrence.status = "acknowledged"
                    current_occurrence.completed_at = instant
                    current_occurrence.updated_at = instant
                else:
                    current.next_notify_at = instant + timedelta(seconds=repeat_interval)
            elif current_occurrence is None or current_occurrence.status != "active":
                cancel_after_accept = True
        if cancel_after_accept:
            await self._cancel_pending_deliveries(
                occurrence.reminder_id, occurrence_id=occurrence.id
            )
        return acceptance

    async def _release_recipient_claim(self, recipient_id: str, worker_id: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(ReminderOccurrenceRecipient)
                .where(
                    ReminderOccurrenceRecipient.id == recipient_id,
                    ReminderOccurrenceRecipient.claimed_by == worker_id,
                )
                .values(claimed_by=None, claim_expires_at=None)
            )

    async def _recheck_occurrence_status(
        self, occurrence: ReminderOccurrence, session: AsyncSession, now: datetime
    ) -> None:
        pending_count = await session.scalar(
            select(func.count(ReminderOccurrenceRecipient.id)).where(
                ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
            )
        )
        if pending_count == 0:
            ack_count = await session.scalar(
                select(func.count(ReminderOccurrenceRecipient.id)).where(
                    ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                    ReminderOccurrenceRecipient.status == RecipientStatus.ACKNOWLEDGED.value,
                )
            )
            if (ack_count or 0) > 0:
                occurrence.status = "acknowledged"
                occurrence.completed_at = now
            else:
                occurrence.status = "expired"
            occurrence.updated_at = now

            reminder = await session.get(Reminder, occurrence.reminder_id)
            if reminder is not None and (
                reminder.schedule_type == ScheduleType.ONCE.value or reminder.next_run_at is None
            ):
                reminder.status = ReminderStatus.COMPLETED.value
                reminder.updated_at = now

    async def operate_latest_interactive(
        self,
        *,
        sender_wecom_userid: str,
        operation: str,
        incoming_message_id: str | None = None,
        now: datetime | None = None,
    ) -> InteractiveOccurrenceResult:
        """Operate exactly the occurrence last delivered successfully to this WeCom user."""
        allowed = {"complete", "snooze_10", "snooze_30", "ignore_today", "stop"}
        if operation not in allowed:
            raise ReminderError("unsupported interactive reminder operation")

        instant = _utc(now or self._clock.now())
        async with self._sessions() as session, session.begin():
            identity = await session.scalar(
                select(WeComIdentity)
                .join(Person, Person.id == WeComIdentity.person_id)
                .where(
                    WeComIdentity.user_id == sender_wecom_userid,
                    WeComIdentity.active.is_(True),
                    Person.active.is_(True),
                )
            )
            if identity is None:
                raise ReminderPermissionDenied("sender is not recognized")

            incoming: IncomingMessage | None = None
            if incoming_message_id is not None:
                incoming = await session.get(IncomingMessage, incoming_message_id)
                if incoming is None or incoming.sender_external_id != sender_wecom_userid:
                    raise ReminderPermissionDenied("incoming menu event does not belong to sender")
                prior_code = incoming.event_payload.get("menu_result")
                prior_occurrence_id = incoming.event_payload.get("menu_occurrence_id")
                if isinstance(prior_code, str) and isinstance(prior_occurrence_id, str):
                    prior_occurrence = await session.get(ReminderOccurrence, prior_occurrence_id)
                    if prior_occurrence is None:
                        raise ReminderNotFound("previously operated occurrence no longer exists")
                    return InteractiveOccurrenceResult(
                        prior_code,
                        prior_occurrence.title_snapshot,
                        prior_occurrence.reminder_id,
                        prior_occurrence.id,
                    )
            if identity.latest_interactive_occurrence_id is None:
                raise ReminderNotFound("no interactive reminder has been delivered")

            occurrence = await session.get(
                ReminderOccurrence, identity.latest_interactive_occurrence_id
            )
            if occurrence is None:
                raise ReminderNotFound("latest interactive occurrence no longer exists")
            reminder = await session.get(Reminder, occurrence.reminder_id)
            recipient = await session.scalar(
                select(ReminderOccurrenceRecipient).where(
                    ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                    ReminderOccurrenceRecipient.person_id == identity.person_id,
                )
            )
            if reminder is None or recipient is None:
                raise ReminderPermissionDenied("sender is not an occurrence recipient")

            result = InteractiveOccurrenceResult(
                "not_active", occurrence.title_snapshot, reminder.id, occurrence.id
            )
            if occurrence.status != "active" or recipient.status != RecipientStatus.PENDING.value:
                identity.latest_interactive_occurrence_id = None
                if incoming is not None:
                    incoming.event_payload = {
                        **incoming.event_payload,
                        "menu_result": result.code,
                        "menu_occurrence_id": result.occurrence_id,
                    }
                return result

            if operation == "complete":
                recipient_values: dict[str, Any] = {
                    "status": RecipientStatus.ACKNOWLEDGED.value,
                    "acknowledged_at": instant,
                    "acknowledged_by": identity.person_id,
                    "next_notify_at": None,
                }
                result_code = "completed"
            elif operation in {"snooze_10", "snooze_30"}:
                minutes = 10 if operation == "snooze_10" else 30
                recipient_values = {"next_notify_at": instant + timedelta(minutes=minutes)}
                result_code = operation
            elif operation == "ignore_today":
                zone = ZoneInfo(reminder.timezone)
                tomorrow = instant.astimezone(zone).date() + timedelta(days=1)
                recipient_values = {
                    "next_notify_at": datetime.combine(tomorrow, time.min, zone).astimezone(UTC)
                }
                result_code = "ignored_today"
            else:
                recipient_values = {
                    "status": RecipientStatus.CANCELLED.value,
                    "next_notify_at": None,
                }
                result_code = "stopped"

            claimed_version = recipient.version
            claimed = cast(
                CursorResult[Any],
                await session.execute(
                    update(ReminderOccurrenceRecipient)
                    .where(
                        ReminderOccurrenceRecipient.id == recipient.id,
                        ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                        ReminderOccurrenceRecipient.version == claimed_version,
                    )
                    .values(
                        **recipient_values,
                        claimed_by=None,
                        claim_expires_at=None,
                        updated_at=instant,
                        version=claimed_version + 1,
                    )
                ),
            )
            if claimed.rowcount != 1:
                result = InteractiveOccurrenceResult(
                    "not_active", occurrence.title_snapshot, reminder.id, occurrence.id
                )
                if incoming is not None:
                    incoming.event_payload = {
                        **incoming.event_payload,
                        "menu_result": result.code,
                        "menu_occurrence_id": result.occurrence_id,
                    }
                return result

            result = InteractiveOccurrenceResult(
                result_code, occurrence.title_snapshot, reminder.id, occurrence.id
            )
            if operation in {"complete", "stop"}:
                identity.latest_interactive_occurrence_id = None
            cancel_recipient_id: str | None = identity.person_id
            if operation == "complete":
                if occurrence.ack_policy_snapshot == AckPolicy.ANY.value:
                    await session.execute(
                        update(ReminderOccurrenceRecipient)
                        .where(
                            ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                            ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                        )
                        .values(
                            status="cancelled",
                            next_notify_at=None,
                            claimed_by=None,
                            claim_expires_at=None,
                            updated_at=instant,
                            version=ReminderOccurrenceRecipient.version + 1,
                        )
                    )
                    cancel_recipient_id = None
                await self._recheck_occurrence_status(occurrence, session, instant)
                if occurrence.status == "acknowledged":
                    occurrence.completed_by = identity.person_id
            elif operation == "stop":
                pending_count = await session.scalar(
                    select(func.count(ReminderOccurrenceRecipient.id)).where(
                        ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                        ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                    )
                )
                if (pending_count or 0) == 0:
                    occurrence.status = "cancelled"
                    occurrence.completed_at = instant
                    occurrence.completed_by = identity.person_id
                    occurrence.updated_at = instant
                    if (
                        reminder.schedule_type == ScheduleType.ONCE.value
                        or reminder.next_run_at is None
                    ):
                        reminder.status = ReminderStatus.COMPLETED.value
                        reminder.updated_at = instant

            notification_ids = select(Notification.id).where(
                Notification.reminder_occurrence_id == occurrence.id
            )
            delivery_filters: builtins.list[Any] = [
                Delivery.notification_id.in_(notification_ids),
                Delivery.status.in_(("pending", "retry_wait")),
            ]
            if cancel_recipient_id is not None:
                delivery_filters.append(Delivery.recipient_id == cancel_recipient_id)
            await session.execute(
                update(Delivery)
                .where(*delivery_filters)
                .values(
                    status="cancelled",
                    last_error_code="reminder_interactive_operation",
                    last_error_message="reminder changed before delivery",
                    updated_at=instant,
                )
            )

            add_audit(
                session,
                self._clock,
                actor_type="wecom_member",
                actor_id=identity.person_id,
                action=f"reminder.interactive.{operation}",
                resource_type="reminder_occurrence",
                resource_id=occurrence.id,
                details={"result": result.code},
            )
            if incoming is not None:
                incoming.event_payload = {
                    **incoming.event_payload,
                    "menu_result": result.code,
                    "menu_occurrence_id": result.occurrence_id,
                }

        return result

    async def acknowledge(
        self,
        *,
        action_token: str,
        sender_wecom_userid: str,
        now: datetime | None = None,
    ) -> AcknowledgementResult:
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
        instant = _utc(now or self._clock.now())
        occurrence_id: str | None = None
        cancel_recipient_id: str | None = None
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
            if identity is None:
                raise ReminderPermissionDenied("sender is not recognized")

            if action.occurrence_recipient_id:
                occ_recipient = await session.get(
                    ReminderOccurrenceRecipient, action.occurrence_recipient_id
                )
                occurrence = await session.get(ReminderOccurrence, action.occurrence_id)
                if occ_recipient is None or occurrence is None:
                    raise ReminderPermissionDenied("occurrence not found")
                if occ_recipient.person_id != identity.person_id:
                    raise ReminderPermissionDenied("sender is not authorized for this action")
                if occurrence.status == "acknowledged":
                    return AcknowledgementResult(
                        "already_completed", occurrence.reminder_id, True, 0
                    )
                if occurrence.status != "active":
                    return AcknowledgementResult("not_active", occurrence.reminder_id, False, 0)
                if _utc(action.expires_at) < instant:
                    raise ReminderPermissionDenied("action token has expired")
                if occ_recipient.status == RecipientStatus.ACKNOWLEDGED.value:
                    return AcknowledgementResult(
                        "already_acknowledged", occurrence.reminder_id, False, 0
                    )

                occ_recipient.status = RecipientStatus.ACKNOWLEDGED.value
                occ_recipient.acknowledged_at = instant
                occ_recipient.acknowledged_by = identity.person_id
                occ_recipient.next_notify_at = None
                occ_recipient.claimed_by = None
                occ_recipient.claim_expires_at = None
                action.consumed_at = instant
                occurrence_id = occurrence.id
                cancel_recipient_id = identity.person_id

                if occurrence.ack_policy_snapshot == AckPolicy.ANY.value:
                    await session.execute(
                        update(ReminderOccurrenceRecipient)
                        .where(
                            ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                            ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                        )
                        .values(status="cancelled", next_notify_at=None)
                    )
                    occurrence.status = "acknowledged"
                    occurrence.completed_at = instant
                    occurrence.completed_by = identity.person_id
                    occurrence.updated_at = instant
                    completed = True
                else:
                    pending_count = await session.scalar(
                        select(func.count(ReminderOccurrenceRecipient.id)).where(
                            ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                            ReminderOccurrenceRecipient.status == RecipientStatus.PENDING.value,
                        )
                    )
                    if pending_count == 0:
                        occurrence.status = "acknowledged"
                        occurrence.completed_at = instant
                        occurrence.completed_by = identity.person_id
                        occurrence.updated_at = instant
                        completed = True
                    else:
                        completed = False
                reminder_id = occurrence.reminder_id
                if completed:
                    reminder = await session.get(Reminder, occurrence.reminder_id)
                    if reminder is not None and (
                        reminder.schedule_type == ScheduleType.ONCE.value
                        or reminder.next_run_at is None
                    ):
                        reminder.status = ReminderStatus.COMPLETED.value
                        reminder.updated_at = instant
            else:
                recipient = await session.get(ReminderRecipient, action.recipient_id)
                reminder = await session.get(Reminder, action.reminder_id)
                if recipient is None or reminder is None:
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
                reminder_id = reminder.id

        if occurrence_id is not None:
            cancelled = await self._cancel_pending_deliveries(
                reminder_id,
                occurrence_id=occurrence_id,
                recipient_id=None if completed else cancel_recipient_id,
            )
        else:
            cancelled = await self._cancel_pending_deliveries(reminder_id) if completed else 0
        return AcknowledgementResult(
            "completed" if completed else "acknowledged", reminder_id, completed, cancelled
        )

    async def _transition(
        self, reminder_id: str, operation: str, *, now: datetime | None
    ) -> Reminder:
        instant = _utc(now or self._clock.now())
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

    async def _cancel_pending_deliveries(
        self,
        reminder_id: str,
        *,
        occurrence_id: str | None = None,
        recipient_id: str | None = None,
    ) -> int:
        return await self._deliveries.cancel_pending(
            reminder_id,
            occurrence_id=occurrence_id,
            recipient_id=recipient_id,
        )

    async def _cancel_deliveries_if_inactive(self, reminder_id: str) -> int:
        async with self._sessions() as session:
            status = await session.scalar(select(Reminder.status).where(Reminder.id == reminder_id))
        if status == ReminderStatus.ACTIVE.value:
            return 0
        return await self._cancel_pending_deliveries(reminder_id)
