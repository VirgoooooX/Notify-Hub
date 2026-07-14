from datetime import UTC, datetime, timedelta

import pytest
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
    with pytest.raises(ReminderError, match="at least 300"):
        validate_continuous_limits(
            require_ack=True,
            repeat_interval_seconds=299,
            max_reminders=12,
            stop_at=start + timedelta(hours=1),
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


def test_action_tokens_are_hashed_and_comparable() -> None:
    token, digest = issue_action_token()
    assert token != digest
    assert len(digest) == 64
    assert action_token_matches(token, digest)
    assert not action_token_matches(token + "x", digest)
