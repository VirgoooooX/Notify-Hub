from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.reminder_schedules import ScheduleValidationError, next_cron_occurrence
from app.plugin_runtime.manifest import CronSchedule, IntervalSchedule, PluginSchedule


class ScheduleError(ValueError):
    pass


def next_run_at(schedule: PluginSchedule, after: datetime) -> datetime:
    if after.tzinfo is None or after.utcoffset() is None:
        raise ScheduleError("datetime must include timezone")
    base = after.astimezone(UTC)
    if isinstance(schedule, IntervalSchedule):
        # Plugin intervals are elapsed-time schedules. They intentionally do
        # not convert through the configured wall-clock timezone.
        return base + timedelta(seconds=schedule.seconds)
    return _next_cron(schedule, base)


def _next_cron(schedule: CronSchedule, after: datetime) -> datetime:
    """Compatibility wrapper around the shared reminder cron evaluator."""

    try:
        candidate = next_cron_occurrence(
            schedule.expression,
            timezone=schedule.timezone,
            after=after,
        )
    except ScheduleValidationError as exc:
        raise ScheduleError(str(exc)) from exc
    if candidate is None:
        raise ScheduleError("cron expression has no occurrence within five years")
    return candidate
