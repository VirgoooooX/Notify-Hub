"""Shared instant and local-wall-time conversion helpers."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def ensure_utc(value: datetime, *, field: str = "datetime") -> datetime:
    """Require an RFC3339 offset and normalize an instant to aware UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return value.astimezone(UTC)


def resolve_datetime(value: datetime, timezone: str, *, field: str = "datetime") -> datetime:
    """Resolve an API datetime as an instant or timezone-local wall time.

    Offset-bearing values are already instants and are normalized directly.
    Naive values are interpreted in the supplied IANA zone.  For a repeated
    DST time ``fold=0`` is chosen consistently; a skipped/nonexistent local
    time is rejected instead of silently shifting it.
    """

    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone}") from exc
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)

    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        # ZoneInfo silently accepts gaps.  A round-trip is the portable way to
        # distinguish a real local time from one that does not exist.
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value:
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f"{field} is not a valid local time in {timezone}")
    return candidates[0].astimezone(UTC)
