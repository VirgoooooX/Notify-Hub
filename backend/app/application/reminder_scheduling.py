from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from app.domain.reminder_schedules import (
    CronSchedule,
    IntervalSchedule,
    OnceSchedule,
    ReminderSchedule,
    ScheduleValidationError,
)
from app.domain.reminders import ReminderError, ScheduleType
from app.infrastructure.database.reminder_models import Reminder


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def structured_schedule(
    *,
    schedule_type: ScheduleType,
    timezone: str,
    scheduled_at: datetime | None,
    interval_seconds: int | None,
    cron_expression: str | None,
    start_at: datetime | None,
) -> ReminderSchedule:
    try:
        if schedule_type is ScheduleType.ONCE:
            if scheduled_at is None:
                raise ReminderError("once schedule requires scheduled_at")
            return OnceSchedule(scheduled_at, timezone)
        if schedule_type is ScheduleType.INTERVAL:
            if interval_seconds is None:
                raise ReminderError("interval schedule requires interval_seconds")
            anchor_at = start_at or scheduled_at
            if anchor_at is None:
                raise ReminderError("interval schedule requires start_at")
            return IntervalSchedule(interval_seconds, anchor_at, timezone)
        if schedule_type is ScheduleType.CRON:
            if not cron_expression:
                raise ReminderError("cron schedule requires cron_expression")
            return CronSchedule(cron_expression, timezone)
    except ScheduleValidationError as exc:
        raise ReminderError(str(exc)) from exc
    raise ReminderError("structured schedule type is required")


def schedule_from_reminder(reminder: Reminder) -> ReminderSchedule | None:
    schedule_type = ScheduleType(reminder.schedule_type)
    if schedule_type is ScheduleType.RECURRING:
        return None
    config: dict[str, Any] = reminder.schedule_config or {}
    return structured_schedule(
        schedule_type=schedule_type,
        timezone=reminder.timezone,
        scheduled_at=utc_datetime(reminder.scheduled_at) if reminder.scheduled_at else None,
        interval_seconds=cast(int | None, config.get("seconds")),
        cron_expression=cast(str | None, config.get("expression")),
        start_at=utc_datetime(reminder.start_at) if reminder.start_at else None,
    )
