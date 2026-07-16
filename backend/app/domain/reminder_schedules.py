from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleValidationError(ValueError):
    """Stable error raised when a reminder schedule cannot be evaluated."""


class MisfirePolicy(str, enum.Enum):
    FIRE_ONCE = "fire_once"
    SKIP = "skip"


def _normalize_datetime(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleValidationError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _load_timezone(name: str) -> ZoneInfo:
    if not name or name != name.strip():
        raise ScheduleValidationError("timezone must be a valid IANA timezone name")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleValidationError(f"unknown timezone: {name}") from exc


@dataclass(frozen=True, slots=True)
class OnceSchedule:
    run_at: datetime
    timezone: str

    def __post_init__(self) -> None:
        _load_timezone(self.timezone)
        object.__setattr__(self, "run_at", _normalize_datetime(self.run_at, field="run_at"))


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    seconds: int
    anchor_at: datetime
    timezone: str

    def __post_init__(self) -> None:
        if isinstance(self.seconds, bool) or self.seconds < 60:
            raise ScheduleValidationError("interval must be at least 60 seconds")
        _load_timezone(self.timezone)
        object.__setattr__(
            self,
            "anchor_at",
            _normalize_datetime(self.anchor_at, field="anchor_at"),
        )


@dataclass(frozen=True, slots=True)
class CronSchedule:
    expression: str
    timezone: str
    _zone: ZoneInfo = field(init=False, repr=False, compare=False)
    _parsed: _CronFields = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        zone = _load_timezone(self.timezone)
        parsed = _parse_cron(self.expression)
        object.__setattr__(self, "expression", " ".join(self.expression.split()))
        object.__setattr__(self, "_zone", zone)
        object.__setattr__(self, "_parsed", parsed)


type ReminderSchedule = OnceSchedule | IntervalSchedule | CronSchedule


@dataclass(frozen=True, slots=True)
class DueResolution:
    """Planner decision for one due reminder; never represents historical backfill."""

    occurrence_at: datetime | None
    next_trigger_at: datetime | None
    misfired: bool


_MONTH_NAMES = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_WEEKDAY_NAMES = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}


@dataclass(frozen=True, slots=True)
class _CronFields:
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]
    day_wildcard: bool
    weekday_wildcard: bool


def _parse_atom(value: str, *, names: dict[str, int] | None) -> int:
    upper = value.upper()
    if names is not None and upper in names:
        return names[upper]
    try:
        return int(value)
    except ValueError as exc:
        raise ScheduleValidationError(f"invalid cron value: {value}") from exc


def _expand_cron_field(
    field: str,
    *,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None = None,
    sunday_alias: bool = False,
) -> frozenset[int]:
    values: set[int] = set()
    for item in field.split(","):
        if not item:
            raise ScheduleValidationError("cron fields cannot contain empty values")
        base, separator, raw_step = item.partition("/")
        if separator:
            if "/" in raw_step:
                raise ScheduleValidationError("invalid cron step")
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise ScheduleValidationError("invalid cron step") from exc
            if step <= 0:
                raise ScheduleValidationError("cron step must be positive")
        else:
            step = 1

        if base in {"*", "?"}:
            start, end = minimum, maximum
        elif "-" in base:
            raw_start, raw_end = base.split("-", maxsplit=1)
            start = _parse_atom(raw_start, names=names)
            end = _parse_atom(raw_end, names=names)
        else:
            start = _parse_atom(base, names=names)
            end = maximum if separator else start

        allowed_maximum = 7 if sunday_alias else maximum
        if start < minimum or end > allowed_maximum or start > end:
            raise ScheduleValidationError("cron field is out of range")
        values.update(range(start, end + 1, step))

    if sunday_alias and 7 in values:
        values.remove(7)
        values.add(0)
    return frozenset(values)


def _parse_cron(expression: str) -> _CronFields:
    normalized = " ".join(expression.split())
    fields = normalized.split(" ") if normalized else []
    if len(fields) != 5:
        raise ScheduleValidationError("cron expression must contain exactly five fields")
    minute, hour, day, month, weekday = fields
    if "?" in minute or "?" in hour or "?" in month:
        raise ScheduleValidationError("? is only supported for cron day fields")
    return _CronFields(
        minute=_expand_cron_field(minute, minimum=0, maximum=59),
        hour=_expand_cron_field(hour, minimum=0, maximum=23),
        day=_expand_cron_field(day, minimum=1, maximum=31),
        month=_expand_cron_field(month, minimum=1, maximum=12, names=_MONTH_NAMES),
        weekday=_expand_cron_field(
            weekday,
            minimum=0,
            maximum=6,
            names=_WEEKDAY_NAMES,
            sunday_alias=True,
        ),
        day_wildcard=day in {"*", "?"},
        weekday_wildcard=weekday in {"*", "?"},
    )


def _cron_calendar_matches(candidate: datetime, fields: _CronFields) -> bool:
    cron_weekday = (candidate.weekday() + 1) % 7
    day_matches = candidate.day in fields.day
    weekday_matches = cron_weekday in fields.weekday
    return (
        day_matches and weekday_matches
        if fields.day_wildcard or fields.weekday_wildcard
        else day_matches or weekday_matches
    )


def _next_cron(
    schedule: CronSchedule,
    *,
    after: datetime,
    inclusive: bool,
) -> datetime | None:
    zone = schedule._zone
    fields = schedule._parsed
    current_date = after.astimezone(zone).date()
    limit_date = current_date + timedelta(days=366 * 5)
    while current_date <= limit_date:
        calendar_probe = datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            tzinfo=zone,
        )
        if current_date.month in fields.month and _cron_calendar_matches(calendar_probe, fields):
            for hour in sorted(fields.hour):
                for minute in sorted(fields.minute):
                    local = calendar_probe.replace(hour=hour, minute=minute, fold=0)
                    candidate = local.astimezone(UTC)
                    if candidate < after or (candidate == after and not inclusive):
                        continue
                    # A round trip rejects nonexistent wall times. fold=0 selects the
                    # first occurrence when clocks repeat a wall-clock minute.
                    round_trip = candidate.astimezone(zone)
                    if round_trip.replace(tzinfo=None) != local.replace(tzinfo=None):
                        continue
                    return candidate
        current_date += timedelta(days=1)
    return None


def _timedelta_microseconds(value: timedelta) -> int:
    return ((value.days * 86_400) + value.seconds) * 1_000_000 + value.microseconds


def _next_interval(
    schedule: IntervalSchedule,
    *,
    after: datetime,
    inclusive: bool,
) -> datetime:
    period_us = schedule.seconds * 1_000_000
    delta_us = _timedelta_microseconds(after - schedule.anchor_at)
    if delta_us < 0:
        index = 0
    elif inclusive:
        index = (delta_us + period_us - 1) // period_us
    else:
        index = (delta_us // period_us) + 1
    return schedule.anchor_at + timedelta(seconds=index * schedule.seconds)


def next_occurrence(
    schedule: ReminderSchedule,
    *,
    after: datetime,
    inclusive: bool = False,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> datetime | None:
    """Return a UTC occurrence respecting inclusive start and end boundaries."""

    normalized_after = _normalize_datetime(after, field="after")
    normalized_start = (
        _normalize_datetime(start_at, field="start_at") if start_at is not None else None
    )
    normalized_end = _normalize_datetime(end_at, field="end_at") if end_at is not None else None
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_start > normalized_end
    ):
        raise ScheduleValidationError("start_at must not be after end_at")

    effective_after = normalized_after
    effective_inclusive = inclusive
    if normalized_start is not None and normalized_start > normalized_after:
        effective_after = normalized_start
        effective_inclusive = True

    if isinstance(schedule, OnceSchedule):
        candidate = schedule.run_at
        if candidate < effective_after or (
            candidate == effective_after and not effective_inclusive
        ):
            return None
    elif isinstance(schedule, IntervalSchedule):
        candidate = _next_interval(
            schedule,
            after=effective_after,
            inclusive=effective_inclusive,
        )
    else:
        cron_candidate = _next_cron(
            schedule,
            after=effective_after,
            inclusive=effective_inclusive,
        )
        if cron_candidate is None:
            return None
        candidate = cron_candidate

    if normalized_start is not None and candidate < normalized_start:
        return None
    if normalized_end is not None and candidate > normalized_end:
        return None
    return candidate


def preview_occurrences(
    schedule: ReminderSchedule,
    *,
    after: datetime,
    count: int = 5,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> tuple[datetime, ...]:
    if isinstance(count, bool) or count < 1 or count > 100:
        raise ScheduleValidationError("preview count must be between 1 and 100")
    occurrences: list[datetime] = []
    cursor = _normalize_datetime(after, field="after")
    while len(occurrences) < count:
        candidate = next_occurrence(
            schedule,
            after=cursor,
            start_at=start_at,
            end_at=end_at,
        )
        if candidate is None:
            break
        occurrences.append(candidate)
        cursor = candidate
    return tuple(occurrences)


def validate_minimum_frequency(
    schedule: ReminderSchedule,
    *,
    after: datetime,
    minimum_seconds: int = 60,
) -> None:
    if isinstance(minimum_seconds, bool) or minimum_seconds < 60:
        raise ScheduleValidationError("minimum frequency must be at least 60 seconds")
    if isinstance(schedule, OnceSchedule):
        return
    occurrences = preview_occurrences(schedule, after=after, count=10)
    if any(
        (later - earlier).total_seconds() < minimum_seconds
        for earlier, later in pairwise(occurrences)
    ):
        raise ScheduleValidationError(
            f"schedule frequency must be at least {minimum_seconds} seconds"
        )


def validate_schedule(
    schedule: ReminderSchedule,
    *,
    now: datetime,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    minimum_seconds: int = 60,
) -> datetime:
    """Validate creation-time rules and return the first future UTC occurrence."""

    instant = _normalize_datetime(now, field="now")
    if isinstance(schedule, OnceSchedule) and schedule.run_at <= instant:
        raise ScheduleValidationError("run_at must be in the future")
    first = next_occurrence(
        schedule,
        after=instant,
        start_at=start_at,
        end_at=end_at,
    )
    if first is None:
        raise ScheduleValidationError("schedule has no future occurrence within its boundaries")
    validate_minimum_frequency(
        schedule,
        after=instant,
        minimum_seconds=minimum_seconds,
    )
    return first


def resolve_due(
    schedule: ReminderSchedule,
    *,
    scheduled_for: datetime,
    now: datetime,
    policy: MisfirePolicy,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> DueResolution:
    """Resolve one due row and advance directly beyond now to prevent bulk backfill."""

    due = _normalize_datetime(scheduled_for, field="scheduled_for")
    instant = _normalize_datetime(now, field="now")
    if due > instant:
        raise ScheduleValidationError("scheduled_for is not due yet")

    misfired = due < instant
    occurrence_at = None if misfired and policy is MisfirePolicy.SKIP else due
    if isinstance(schedule, OnceSchedule):
        next_trigger_at = None
    else:
        next_trigger_at = next_occurrence(
            schedule,
            after=instant if misfired else due,
            start_at=start_at,
            end_at=end_at,
        )
    return DueResolution(
        occurrence_at=occurrence_at,
        next_trigger_at=next_trigger_at,
        misfired=misfired,
    )
