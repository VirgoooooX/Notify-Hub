"""Time rules for sends inside one interactive reminder occurrence."""

from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any, Literal

from app.domain.reminder_schedules import CronSchedule, next_occurrence, validate_minimum_frequency
from app.domain.reminders import ReminderError, normalize_utc, validate_continuous_limits
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class RepeatRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["interval", "cron", "offsets", "before_due"] = "interval"
    interval_seconds: int | None = Field(default=None, ge=300)
    cron_expression: str | None = Field(default=None, max_length=200)
    offsets_seconds: list[StrictInt] | None = Field(default=None, min_length=1, max_length=12)
    due_at: datetime | None = None
    days_before_due: list[StrictInt] | None = Field(default=None, min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_rule(self) -> "RepeatRule":
        allowed = {
            "interval": {"interval_seconds"},
            "cron": {"cron_expression"},
            "offsets": {"offsets_seconds"},
            "before_due": {"due_at", "days_before_due"},
        }[self.type]
        for name in {
            "interval_seconds",
            "cron_expression",
            "offsets_seconds",
            "due_at",
            "days_before_due",
        } - allowed:
            if getattr(self, name) is not None:
                raise ValueError(f"{name} is not supported for {self.type} repeats")
        if self.type == "cron" and not self.cron_expression:
            raise ValueError("cron repeats require cron_expression")
        if self.type == "offsets":
            offsets = self.offsets_seconds
            if not offsets or offsets[0] != 0 or offsets != sorted(set(offsets)):
                raise ValueError("offsets_seconds must start at 0 and be strictly increasing")
            if offsets[-1] > 30 * 86400 or any(b - a < 300 for a, b in pairwise(offsets)):
                raise ValueError(
                    "repeat nodes must be at least 300 seconds apart and within 30 days"
                )
        if self.type == "before_due":
            days = self.days_before_due
            if self.due_at is None or not days or any(day < 0 or day > 30 for day in days):
                raise ValueError("before_due requires due_at and days between 0 and 30")
            if days != sorted(set(days), reverse=True):
                raise ValueError("days_before_due must be unique and descending")
        return self


def parse_repeat_rule(config: dict[str, Any] | None) -> RepeatRule:
    try:
        return RepeatRule.model_validate(config or {})
    except ValueError as exc:
        raise ReminderError("invalid repeat rule") from exc


def due_repeat_start(rule: RepeatRule, *, now: datetime) -> datetime:
    assert rule.due_at is not None and rule.days_before_due is not None
    due_at = normalize_utc(rule.due_at)
    for day in rule.days_before_due:
        candidate = due_at - timedelta(days=day)
        if candidate >= now:
            return candidate
    raise ReminderError("all repeat nodes are in the past")


def validate_repeat_rule(rule: RepeatRule, *, timezone: str, start_at: datetime) -> None:
    if rule.type == "cron":
        try:
            schedule = CronSchedule(rule.cron_expression or "", timezone)
            validate_minimum_frequency(schedule, after=start_at, minimum_seconds=300)
        except ValueError as exc:
            raise ReminderError("invalid repeat cron or frequency below 300 seconds") from exc
    if rule.type == "before_due":
        if rule.due_at is None:
            raise ReminderError("due_at is required")
        normalize_utc(rule.due_at)


def next_repeat_at(
    config: dict[str, Any],
    *,
    interval_seconds: int | None,
    scheduled_for: datetime,
    after: datetime,
    timezone: str,
) -> datetime | None:
    """Skip missed nodes; fixed nodes never drift with delivery/worker delay."""
    kind = config.get("type", "interval")
    if kind == "interval":
        return after + timedelta(seconds=interval_seconds or 300)
    if kind == "cron":
        return next_occurrence(CronSchedule(config["cron_expression"], timezone), after=after)
    if kind == "offsets":
        candidates = (
            scheduled_for + timedelta(seconds=offset) for offset in config["offsets_seconds"]
        )
    else:
        due_at = normalize_utc(datetime.fromisoformat(config["due_at"]))
        candidates = (due_at - timedelta(days=day) for day in config["days_before_due"])
    return next((candidate for candidate in candidates if candidate > after), None)


def configure_repeat(
    *,
    require_ack: bool,
    config: dict[str, Any] | None,
    interval_seconds: int | None,
    maximum: int | None,
    stop_at: datetime | None,
    start_at: datetime,
    timezone: str,
) -> tuple[int | None, int | None, datetime | None, dict[str, Any]]:
    rule = parse_repeat_rule(config)
    if not require_ack and rule.type != "interval":
        raise ReminderError("repeat rules require an interactive reminder")
    validate_repeat_rule(rule, timezone=timezone, start_at=start_at)
    if rule.type == "interval":
        interval, maximum, stop = validate_continuous_limits(
            require_ack=require_ack,
            repeat_interval_seconds=rule.interval_seconds or interval_seconds,
            max_reminders=maximum,
            stop_at=stop_at,
            start_at=start_at,
        )
        return interval, maximum, stop, {}
    if not require_ack:
        return None, None, None, {}
    maximum = maximum or 12
    if not 1 <= maximum <= 12:
        raise ReminderError("continuous reminder max_reminders must be between 1 and 12")
    # The deadline is exclusive; allow a node exactly 30 days after the first.
    deadline = start_at + timedelta(days=30, seconds=1)
    stop = normalize_utc(stop_at) if stop_at else deadline
    if stop <= start_at or stop > deadline:
        raise ReminderError("repeat stop_at must be after the first send and within 30 days")
    return None, maximum, stop, rule.model_dump(mode="json", exclude_none=True)
