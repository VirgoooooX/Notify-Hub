from datetime import UTC, datetime, timedelta

import pytest
from app.domain import reminders as reminders_domain
from app.domain.reminders import (
    InvalidReminderTransition,
    ReminderError,
    ReminderSnapshot,
    ReminderStatus,
    action_token_matches,
    issue_action_token,
    next_rrule_occurrence,
    validate_continuous_limits,
)


def test_reminder_state_machine_and_derived_awaiting_ack() -> None:
    due = datetime(2026, 7, 14, tzinfo=UTC)
    draft = ReminderSnapshot(ReminderStatus.DRAFT, True, due)
    active = draft.activate()
    sent = ReminderSnapshot(ReminderStatus.ACTIVE, True, due, reminder_count=1)

    assert active.status is ReminderStatus.ACTIVE
    assert sent.display_status == "awaiting_ack"
    assert sent.complete().status is ReminderStatus.COMPLETED
    assert sent.complete().complete().status is ReminderStatus.COMPLETED
    with pytest.raises(InvalidReminderTransition):
        draft.pause()


def test_continuous_defaults_and_limits() -> None:
    start = datetime(2026, 7, 14, tzinfo=UTC)
    assert validate_continuous_limits(
        require_ack=True,
        repeat_interval_seconds=None,
        max_reminders=None,
        stop_at=None,
        start_at=start,
    ) == (300, 12, start + timedelta(hours=24))
    assert validate_continuous_limits(
        require_ack=True,
        repeat_interval_seconds=86_400,
        max_reminders=3,
        stop_at=None,
        start_at=start,
    ) == (86_400, 3, start + timedelta(days=3))
    with pytest.raises(ReminderError, match="at least 300"):
        validate_continuous_limits(
            require_ack=True,
            repeat_interval_seconds=299,
            max_reminders=12,
            stop_at=start + timedelta(hours=1),
            start_at=start,
        )
    with pytest.raises(ReminderError, match="cannot exceed 30 days"):
        validate_continuous_limits(
            require_ack=True,
            repeat_interval_seconds=86_400,
            max_reminders=3,
            stop_at=start + timedelta(days=31),
            start_at=start,
        )


def test_rrule_uses_configured_timezone() -> None:
    start = datetime(2026, 7, 13, 1, 0, tzinfo=UTC)  # Monday 09:00 Shanghai
    occurrence = next_rrule_occurrence(
        "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
        timezone="Asia/Shanghai",
        after=start,
        dtstart=start,
    )
    assert occurrence == datetime(2026, 7, 20, 1, 0, tzinfo=UTC)


def test_rrule_skips_nonexistent_dst_wall_time() -> None:
    occurrence = next_rrule_occurrence(
        "FREQ=DAILY;BYHOUR=2;BYMINUTE=30",
        timezone="America/New_York",
        after=datetime(2026, 3, 7, 8, tzinfo=UTC),
        dtstart=datetime(2026, 3, 6, 7, 30, tzinfo=UTC),
    )
    assert occurrence == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_rrule_fold_uses_first_repeated_wall_time() -> None:
    occurrence = next_rrule_occurrence(
        "FREQ=DAILY;BYHOUR=1;BYMINUTE=30",
        timezone="America/New_York",
        after=datetime(2026, 10, 31, 6, tzinfo=UTC),
        dtstart=datetime(2026, 10, 30, 5, 30, tzinfo=UTC),
    )
    assert occurrence == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_rrule_rejects_naive_inputs() -> None:
    with pytest.raises(ReminderError, match="must include timezone"):
        next_rrule_occurrence(
            "FREQ=DAILY",
            timezone="UTC",
            after=datetime(2026, 1, 1),
            dtstart=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_rrule_count_counts_valid_wall_times_across_dst_gap() -> None:
    start = datetime(2026, 3, 6, 7, 30, tzinfo=UTC)
    rule = "FREQ=DAILY;COUNT=3;BYHOUR=2;BYMINUTE=30"
    cursor = datetime(2026, 3, 5, tzinfo=UTC)

    occurrences: list[datetime] = []
    for _ in range(3):
        occurrence = next_rrule_occurrence(
            rule,
            timezone="America/New_York",
            after=cursor,
            dtstart=start,
        )
        assert occurrence is not None
        occurrences.append(occurrence)
        cursor = occurrence

    assert occurrences == [
        datetime(2026, 3, 6, 7, 30, tzinfo=UTC),
        datetime(2026, 3, 7, 7, 30, tzinfo=UTC),
        datetime(2026, 3, 9, 6, 30, tzinfo=UTC),
    ]
    assert (
        next_rrule_occurrence(
            rule,
            timezone="America/New_York",
            after=cursor,
            dtstart=start,
        )
        is None
    )


def test_rrule_count_fold_uses_one_wall_time() -> None:
    start = datetime(2026, 10, 30, 5, 30, tzinfo=UTC)
    rule = "FREQ=DAILY;COUNT=3;BYHOUR=1;BYMINUTE=30"
    cursor = datetime(2026, 10, 29, tzinfo=UTC)

    occurrences: list[datetime] = []
    for _ in range(3):
        occurrence = next_rrule_occurrence(
            rule,
            timezone="America/New_York",
            after=cursor,
            dtstart=start,
        )
        assert occurrence is not None
        occurrences.append(occurrence)
        cursor = occurrence

    assert occurrences == [
        datetime(2026, 10, 30, 5, 30, tzinfo=UTC),
        datetime(2026, 10, 31, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
    ]
    assert (
        next_rrule_occurrence(
            rule,
            timezone="America/New_York",
            after=cursor,
            dtstart=start,
        )
        is None
    )


def test_rrule_count_preserves_until_boundary() -> None:
    occurrence = next_rrule_occurrence(
        "FREQ=DAILY;COUNT=3;UNTIL=20260312T050000Z;BYHOUR=2;BYMINUTE=30",
        timezone="America/New_York",
        after=datetime(2026, 3, 7, 8, tzinfo=UTC),
        dtstart=datetime(2026, 3, 6, 7, 30, tzinfo=UTC),
    )
    assert occurrence == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "recurrence_rule",
    [
        "DTSTART:20260306T023000\nRRULE:FREQ=DAILY",
        "FREQ=DAILY;DTSTART=20260306T023000",
    ],
)
def test_rrule_rejects_embedded_dtstart(recurrence_rule: str) -> None:
    with pytest.raises(ReminderError, match="only an RRULE body"):
        next_rrule_occurrence(
            recurrence_rule,
            timezone="America/New_York",
            after=datetime(2026, 3, 5, tzinfo=UTC),
            dtstart=datetime(2026, 3, 6, 7, 30, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "recurrence_rule",
    [
        "FREQ=DAILY;COUNT=10;BYHOUR=2;BYMINUTE=30",
        "FREQ=DAILY;BYHOUR=2;BYMINUTE=30",
    ],
)
def test_rrule_all_invalid_scan_is_bounded(
    recurrence_rule: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reminders_domain, "_RRULE_MAX_CANDIDATES", 3)
    monkeypatch.setattr(reminders_domain, "local_wall_time_to_utc", lambda *_args: None)

    with pytest.raises(ReminderError, match="bounded DST scan"):
        next_rrule_occurrence(
            recurrence_rule,
            timezone="UTC",
            after=datetime(2026, 1, 1, tzinfo=UTC),
            dtstart=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_action_tokens_are_hashed_and_comparable() -> None:
    token, digest = issue_action_token()
    assert token != digest
    assert len(digest) == 64
    assert action_token_matches(token, digest)
    assert not action_token_matches(token + "x", digest)
