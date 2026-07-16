from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from app.application.audit import add_audit
from app.application.reminder_service import ReminderCreate, ReminderService
from app.domain.clock import Clock
from app.domain.reminders import ReminderError, ScheduleType, normalize_utc
from app.infrastructure.database.models import AuditLog
from app.infrastructure.database.reminder_models import Reminder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ReminderAccessDenied(ReminderError):
    """The actor is authenticated but is not allowed to create this reminder."""


class ReminderQuotaExceeded(ReminderError):
    """The actor has reached its configured active-reminder quota."""


class ReminderIdempotencyConflict(ReminderError):
    """An idempotency key was reused for a different reminder command."""


@dataclass(frozen=True, slots=True)
class ReminderActor:
    actor_type: Literal["api_client", "plugin"]
    actor_id: str


@dataclass(frozen=True, slots=True)
class ReminderPermissions:
    allow_create: bool = False
    allow_recurring: bool = False
    allow_cron: bool = False
    allow_interactive: bool = False
    allow_media: bool = False
    allowed_recipients: tuple[str, ...] = ()
    max_active: int = 0
    min_interval_seconds: int = 300
    max_duration_seconds: int = 86_400
    max_notifications: int = 12


@dataclass(frozen=True, slots=True)
class ReminderCreationResult:
    reminder: Reminder
    duplicate: bool


class ReminderAccessService:
    """Authorize constrained actors before delegating to the reminder core.

    This service deliberately exposes neither an ORM session nor Reminder tables to
    plugins and API routes.  ReminderService remains the only reminder writer.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reminder_service: ReminderService,
        clock: Clock,
    ) -> None:
        self._sessions = session_factory
        self._reminders = reminder_service
        self._clock = clock
        self._actor_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def create(
        self,
        command: ReminderCreate,
        *,
        actor: ReminderActor,
        permissions: ReminderPermissions,
        schedule_mode: Literal["once", "interval", "cron", "recurring"] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> ReminderCreationResult:
        self._authorize(command, permissions, schedule_mode=schedule_mode)
        if idempotency_key is not None and not (1 <= len(idempotency_key) <= 200):
            raise ReminderError("idempotency key must contain 1 to 200 characters")
        lock = self._actor_locks.setdefault((actor.actor_type, actor.actor_id), asyncio.Lock())
        command_hash = self._command_hash(command, schedule_mode)
        async with lock:
            if idempotency_key is not None:
                existing = await self._find_idempotent(actor, idempotency_key, command_hash)
                if existing is not None:
                    return ReminderCreationResult(existing, duplicate=True)
            await self._enforce_quota(actor, permissions.max_active)
            reminder = await self._reminders.create(command)
            async with self._sessions() as session, session.begin():
                add_audit(
                    session,
                    self._clock,
                    actor_type=actor.actor_type,
                    actor_id=actor.actor_id,
                    action="reminder.create",
                    resource_type="reminder",
                    resource_id=reminder.id,
                    request_id=request_id,
                    details={
                        "recipients": list(command.recipient_ids),
                        "schedule_mode": schedule_mode or command.schedule_type.value,
                        "interactive": command.require_ack,
                        "content_type": command.content_type,
                        "idempotency_key": idempotency_key,
                        "command_hash": command_hash,
                    },
                )
            return ReminderCreationResult(reminder, duplicate=False)

    @staticmethod
    def _authorize(
        command: ReminderCreate,
        permissions: ReminderPermissions,
        *,
        schedule_mode: str | None,
    ) -> None:
        if not permissions.allow_create:
            raise ReminderAccessDenied("reminder creation is not permitted")

        recurring = command.schedule_type is not ScheduleType.ONCE
        if recurring and not permissions.allow_recurring:
            raise ReminderAccessDenied("recurring reminders are not permitted")
        if (
            command.schedule_type is ScheduleType.CRON or schedule_mode == "cron"
        ) and not permissions.allow_cron:
            raise ReminderAccessDenied("cron reminders are not permitted")
        if command.require_ack and not permissions.allow_interactive:
            raise ReminderAccessDenied("interactive reminders are not permitted")
        if (
            command.content_type != "text" or command.media_asset_id
        ) and not permissions.allow_media:
            raise ReminderAccessDenied("media reminders are not permitted")

        allowed = set(permissions.allowed_recipients)
        for recipient_id in command.recipient_ids:
            if recipient_id in allowed:
                continue
            raise ReminderAccessDenied(f"recipient is not permitted: {recipient_id}")

        if (
            command.repeat_interval_seconds is not None
            and command.repeat_interval_seconds < permissions.min_interval_seconds
        ):
            raise ReminderAccessDenied("reminder interval is below the permitted minimum")
        if (
            command.interval_seconds is not None
            and command.interval_seconds < permissions.min_interval_seconds
        ):
            raise ReminderAccessDenied("schedule interval is below the permitted minimum")
        if (
            command.max_reminders is not None
            and command.max_reminders > permissions.max_notifications
        ):
            raise ReminderAccessDenied("notification count exceeds the permitted maximum")
        if command.stop_at is not None and command.scheduled_at is not None:
            duration = (
                normalize_utc(command.stop_at) - normalize_utc(command.scheduled_at)
            ).total_seconds()
            if duration > permissions.max_duration_seconds:
                raise ReminderAccessDenied("reminder duration exceeds the permitted maximum")

    async def _enforce_quota(self, actor: ReminderActor, maximum: int) -> None:
        if maximum < 1:
            raise ReminderQuotaExceeded("active reminder quota is zero")
        async with self._sessions() as session:
            active_count = await session.scalar(
                select(func.count(Reminder.id))
                .join(
                    AuditLog,
                    (AuditLog.resource_type == "reminder")
                    & (AuditLog.resource_id == Reminder.id)
                    & (AuditLog.action == "reminder.create"),
                )
                .where(
                    AuditLog.actor_type == actor.actor_type,
                    AuditLog.actor_id == actor.actor_id,
                    Reminder.status.in_(("active", "paused")),
                )
            )
        if int(active_count or 0) >= maximum:
            raise ReminderQuotaExceeded("active reminder quota exceeded")

    async def _find_idempotent(
        self, actor: ReminderActor, idempotency_key: str, command_hash: str
    ) -> Reminder | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.actor_type == actor.actor_type,
                    AuditLog.actor_id == actor.actor_id,
                    AuditLog.action == "reminder.create",
                    AuditLog.details["idempotency_key"].as_string() == idempotency_key,
                )
                .order_by(AuditLog.created_at.desc())
            )
            if row is None or not row.resource_id:
                return None
            if row.details.get("command_hash") != command_hash:
                raise ReminderIdempotencyConflict(
                    "idempotency key was already used for a different reminder"
                )
            reminder = await session.get(Reminder, row.resource_id)
            if reminder is not None:
                return reminder
        return None

    @staticmethod
    def _command_hash(command: ReminderCreate, schedule_mode: str | None) -> str:
        payload = {
            "creator_person_id": command.creator_person_id,
            "title": command.title,
            "content": command.content,
            "schedule_type": command.schedule_type.value,
            "schedule_mode": schedule_mode,
            "timezone": command.timezone,
            "recipient_ids": list(command.recipient_ids),
            "scheduled_at": command.scheduled_at.isoformat() if command.scheduled_at else None,
            "recurrence_rule": command.recurrence_rule,
            "interval_seconds": command.interval_seconds,
            "cron_expression": command.cron_expression,
            "start_at": command.start_at.isoformat() if command.start_at else None,
            "end_at": command.end_at.isoformat() if command.end_at else None,
            "misfire_policy": command.misfire_policy.value,
            "require_ack": command.require_ack,
            "ack_policy": command.ack_policy.value,
            "repeat_interval_seconds": command.repeat_interval_seconds,
            "max_reminders": command.max_reminders,
            "stop_at": command.stop_at.isoformat() if command.stop_at else None,
            "content_type": command.content_type,
            "media_asset_id": command.media_asset_id,
            "url": command.url,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()
