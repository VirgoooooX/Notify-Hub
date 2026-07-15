from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.plugin_runtime.manifest import CronSchedule, IntervalSchedule, PluginSchedule


class ScheduleError(ValueError):
    pass


def next_run_at(schedule: PluginSchedule, after: datetime) -> datetime:
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    base = after.astimezone(UTC)
    if isinstance(schedule, IntervalSchedule):
        return base + timedelta(seconds=schedule.seconds)
    return _next_cron(schedule, base)


def _expand(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", maxsplit=1)
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise ScheduleError("invalid cron step") from exc
            if step <= 0:
                raise ScheduleError("cron step must be positive")
        if part in {"*", "?"}:
            start, end = minimum, maximum
        elif "-" in part:
            raw_start, raw_end = part.split("-", maxsplit=1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(part)
        if start < minimum or end > maximum or start > end:
            raise ScheduleError("cron field is out of range")
        values.update(range(start, end + 1, step))
    return values


def _next_cron(schedule: CronSchedule, after: datetime) -> datetime:
    try:
        timezone = ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"unknown timezone {schedule.timezone}") from exc
    fields = schedule.expression.split()
    minute, hour, day, month, weekday = (
        _expand(fields[0], 0, 59),
        _expand(fields[1], 0, 23),
        _expand(fields[2], 1, 31),
        _expand(fields[3], 1, 12),
        _expand(fields[4], 0, 7),
    )
    weekday = {0 if item == 7 else item for item in weekday}
    day_is_wildcard = fields[2] in {"*", "?"}
    weekday_is_wildcard = fields[4] in {"*", "?"}
    candidate = after.astimezone(timezone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + timedelta(days=366 * 5)
    while candidate <= limit:
        cron_weekday = (candidate.weekday() + 1) % 7
        day_matches = candidate.day in day
        weekday_matches = cron_weekday in weekday
        calendar_matches = (
            day_matches and weekday_matches
            if day_is_wildcard or weekday_is_wildcard
            else day_matches or weekday_matches
        )
        if (
            candidate.minute in minute
            and candidate.hour in hour
            and candidate.month in month
            and calendar_matches
        ):
            return candidate.astimezone(UTC)
        candidate += timedelta(minutes=1)
    raise ScheduleError("cron expression has no occurrence within five years")
