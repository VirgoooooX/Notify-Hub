from __future__ import annotations

import enum
import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.reminder_schedules import local_wall_time_to_utc
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


_RRULE_MAX_CANDIDATES = 100_000


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
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderError("datetime must include timezone")
    return value.astimezone(UTC)


def _prepare_rrule_text(recurrence_rule: str) -> tuple[str, int | None]:
    """Validate the rule-body contract and remove COUNT for valid counting.

    dateutil counts every generated wall-clock candidate, including a local
    time in a DST gap.  We therefore remove COUNT and count only candidates
    that pass the IANA round-trip in ``next_rrule_occurrence``.  Embedded
    DTSTART or multi-line recurrence sets are rejected so the explicit
    ``timezone`` and ``dtstart`` arguments remain authoritative.
    """

    if not isinstance(recurrence_rule, str):
        raise ReminderError("recurrence rule must contain only an RRULE body")
    if "\n" in recurrence_rule or "\r" in recurrence_rule:
        raise ReminderError("recurrence rule must contain only an RRULE body")
    text = recurrence_rule.strip()
    upper_text = text.upper()
    if not text or "DTSTART" in upper_text:
        raise ReminderError("recurrence rule must contain only an RRULE body")
    if upper_text.startswith("RRULE:"):
        text = text[6:].strip()
    if not text.upper().startswith("FREQ="):
        raise ReminderError("recurrence rule must contain an RRULE body")

    raw_count: str | None = None
    retained_parts: list[str] = []
    for part in text.split(";"):
        key, separator, raw_value = part.partition("=")
        if separator and key.strip().upper() == "COUNT":
            raw_count = raw_value.strip()
            continue
        retained_parts.append(part)
    count: int | None = None
    if raw_count is not None:
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ReminderError("invalid recurrence rule") from exc
    return ";".join(retained_parts), count


def next_rrule_occurrence(
    recurrence_rule: str,
    *,
    timezone: str,
    after: datetime,
    dtstart: datetime,
) -> datetime | None:
    zone = validate_timezone(timezone)
    rule_text, count = _prepare_rrule_text(recurrence_rule)
    normalized_after = normalize_utc(after)
    local_start = normalize_utc(dtstart).astimezone(zone)
    if count is not None and count <= 0:
        return None
    local_after = normalized_after.astimezone(zone)
    try:
        rule = rrulestr(rule_text, dtstart=local_start)
    except (TypeError, ValueError) as exc:
        raise ReminderError("invalid recurrence rule") from exc

    if count is None:
        candidate = rule.after(local_after, inc=False)
        scanned = 0
        while candidate is not None:
            scanned += 1
            if scanned > _RRULE_MAX_CANDIDATES:
                raise ReminderError("recurrence rule exceeded bounded DST scan")
            # dateutil returns a timezone-aware value for an RRULE with an
            # aware DTSTART, but preserve support for a rule that yields naive
            # values.
            candidate_local = (
                candidate.replace(tzinfo=zone)
                if candidate.tzinfo is None
                else candidate.astimezone(zone)
            )
            # Validate the local wall-clock fields rather than trusting the
            # offset dateutil attached. This skips spring-forward gaps and
            # chooses fold=0.
            candidate_utc = local_wall_time_to_utc(candidate_local, zone)
            if candidate_utc is not None and candidate_utc > normalized_after:
                return candidate_utc
            try:
                candidate = rule.after(candidate, inc=False)
            except (TypeError, ValueError) as exc:
                raise ReminderError("invalid recurrence rule") from exc
        return None

    # COUNT is defined over the recurrence set. Iterate from DTSTART so an
    # invalid wall time does not consume the count before the requested `after`
    # boundary. The bound prevents a pathological all-gap rule from looping
    # forever when no UNTIL terminates its recurrence set.
    # Probe just before the DTSTART second so a rule whose DTSTART has
    # sub-second precision still includes its first wall-clock candidate.
    try:
        scan_cursor = local_start.replace(microsecond=0) - timedelta(microseconds=1)
        candidate = rule.after(scan_cursor, inc=False)
    except OverflowError:
        candidate = rule.after(local_start, inc=True)
    valid_count = 0
    scanned = 0
    seen_wall_times: set[datetime] = set()
    while candidate is not None:
        scanned += 1
        if scanned > _RRULE_MAX_CANDIDATES:
            raise ReminderError("recurrence rule exceeded bounded DST scan")
        candidate_local = (
            candidate.replace(tzinfo=zone)
            if candidate.tzinfo is None
            else candidate.astimezone(zone)
        )
        wall_time = candidate_local.replace(tzinfo=None)
        if wall_time in seen_wall_times:
            candidate_utc = None
        else:
            seen_wall_times.add(wall_time)
            candidate_utc = local_wall_time_to_utc(candidate_local, zone)
        if candidate_utc is not None:
            valid_count += 1
            if valid_count > count:
                return None
            if candidate_utc > normalized_after:
                return candidate_utc
            if valid_count == count:
                return None
        try:
            candidate = rule.after(candidate, inc=False)
        except (TypeError, ValueError) as exc:
            raise ReminderError("invalid recurrence rule") from exc
    return None


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
