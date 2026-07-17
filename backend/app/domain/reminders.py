from __future__ import annotations

import enum
import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr


class ReminderError(ValueError):
    """Stable domain error raised for invalid reminder operations."""


class InvalidReminderTransition(ReminderError):
    pass


class ReminderStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ScheduleType(str, enum.Enum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"
    RECURRING = "recurring"


class AckPolicy(str, enum.Enum):
    ANY = "any"
    ALL = "all"
    EACH = "each"


class RecipientStatus(str, enum.Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ConversationState(str, enum.Enum):
    IDLE = "idle"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_TIME = "awaiting_time"
    AWAITING_RECIPIENT = "awaiting_recipient"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ReminderSnapshot:
    status: ReminderStatus
    require_ack: bool
    next_run_at: datetime | None
    stop_at: datetime | None = None
    reminder_count: int = 0
    max_reminders: int | None = None

    @property
    def display_status(self) -> str:
        if self.status is ReminderStatus.ACTIVE and self.require_ack and self.reminder_count:
            return "awaiting_ack"
        return self.status.value

    def activate(self) -> ReminderSnapshot:
        if self.status not in {ReminderStatus.DRAFT, ReminderStatus.PAUSED}:
            raise InvalidReminderTransition(f"cannot activate {self.status.value}")
        return replace(self, status=ReminderStatus.ACTIVE)

    def pause(self) -> ReminderSnapshot:
        if self.status is not ReminderStatus.ACTIVE:
            raise InvalidReminderTransition(f"cannot pause {self.status.value}")
        return replace(self, status=ReminderStatus.PAUSED)

    def complete(self) -> ReminderSnapshot:
        if self.status in {ReminderStatus.COMPLETED, ReminderStatus.CANCELLED}:
            if self.status is ReminderStatus.COMPLETED:
                return self
            raise InvalidReminderTransition("cancelled reminder cannot be completed")
        if self.status not in {ReminderStatus.ACTIVE, ReminderStatus.PAUSED}:
            raise InvalidReminderTransition(f"cannot complete {self.status.value}")
        return replace(self, status=ReminderStatus.COMPLETED, next_run_at=None)

    def cancel(self) -> ReminderSnapshot:
        if self.status is ReminderStatus.CANCELLED:
            return self
        if self.status not in {
            ReminderStatus.DRAFT,
            ReminderStatus.ACTIVE,
            ReminderStatus.PAUSED,
        }:
            raise InvalidReminderTransition(f"cannot cancel {self.status.value}")
        return replace(self, status=ReminderStatus.CANCELLED, next_run_at=None)

    def expire(self) -> ReminderSnapshot:
        if self.status is not ReminderStatus.ACTIVE:
            raise InvalidReminderTransition(f"cannot expire {self.status.value}")
        return replace(self, status=ReminderStatus.EXPIRED, next_run_at=None)


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ReminderError(f"unknown timezone: {name}") from exc


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ReminderError("datetime must include timezone")
    return value.astimezone(UTC)


def next_rrule_occurrence(
    recurrence_rule: str,
    *,
    timezone: str,
    after: datetime,
    dtstart: datetime,
) -> datetime | None:
    zone = validate_timezone(timezone)
    local_start = normalize_utc(dtstart).astimezone(zone)
    local_after = normalize_utc(after).astimezone(zone)
    try:
        rule = rrulestr(recurrence_rule, dtstart=local_start)
        candidate = rule.after(local_after, inc=False)
    except (TypeError, ValueError) as exc:
        raise ReminderError("invalid recurrence rule") from exc
    if candidate is None:
        return None
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=zone)
    return cast(datetime, candidate.astimezone(UTC))


def validate_continuous_limits(
    *,
    require_ack: bool,
    repeat_interval_seconds: int | None,
    max_reminders: int | None,
    stop_at: datetime | None,
    start_at: datetime,
) -> tuple[int | None, int | None, datetime | None]:
    if not require_ack:
        return None, None, None
    interval = repeat_interval_seconds or 300
    maximum = max_reminders or 12
    if interval < 300:
        raise ReminderError("continuous reminder interval must be at least 300 seconds")
    if maximum < 1 or maximum > 12:
        raise ReminderError("continuous reminder max_reminders must be between 1 and 12")
    maximum_duration = timedelta(days=30)
    default_duration_seconds = max(86_400, interval * maximum)
    if default_duration_seconds > int(maximum_duration.total_seconds()):
        raise ReminderError("continuous reminder duration cannot exceed 30 days")
    default_duration = timedelta(seconds=default_duration_seconds)
    normalized_start = normalize_utc(start_at)
    stop = normalize_utc(stop_at) if stop_at else normalized_start + default_duration
    if stop > normalized_start + maximum_duration:
        raise ReminderError("continuous reminder duration cannot exceed 30 days")
    return interval, maximum, stop


def issue_action_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    return token, hash_action_token(token)


def hash_action_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def action_token_matches(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_action_token(token), expected_hash)
