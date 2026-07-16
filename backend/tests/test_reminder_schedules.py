from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from app.domain.reminder_schedules import (
    CronSchedule,
    IntervalSchedule,
    MisfirePolicy,
    OnceSchedule,
    ScheduleValidationError,
    next_occurrence,
    preview_occurrences,
    resolve_due,
    validate_minimum_frequency,
    validate_schedule,
)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_once_is_normalized_to_utc_and_only_occurs_once() -> None:
    schedule = OnceSchedule(
        run_at=datetime.fromisoformat("2026-07-17T08:00:00+08:00"),
        timezone="Asia/Shanghai",
    )

    assert next_occurrence(schedule, after=utc(2026, 7, 16)) == utc(2026, 7, 17)
    assert next_occurrence(schedule, after=utc(2026, 7, 17)) is None


def test_interval_uses_anchor_instead_of_worker_run_time() -> None:
    schedule = IntervalSchedule(seconds=300, anchor_at=utc(2026, 7, 16), timezone="UTC")

    assert next_occurrence(schedule, after=utc(2026, 7, 16, 0, 7)) == datetime(
        2026, 7, 16, 0, 10, tzinfo=UTC
    )
    assert next_occurrence(schedule, after=utc(2026, 7, 16, 0, 10)) == datetime(
        2026, 7, 16, 0, 15, tzinfo=UTC
    )


def test_interval_preview_returns_five_runs_without_drift() -> None:
    schedule = IntervalSchedule(seconds=3600, anchor_at=utc(2026, 7, 16), timezone="UTC")

    assert preview_occurrences(schedule, after=utc(2026, 7, 16), count=5) == tuple(
        datetime(2026, 7, 16, hour, tzinfo=UTC) for hour in range(1, 6)
    )


def test_cron_supports_weekdays_and_named_fields() -> None:
    schedule = CronSchedule(expression="0 9 * * MON-FRI", timezone="Asia/Shanghai")

    assert preview_occurrences(schedule, after=utc(2026, 7, 16), count=3) == (
        utc(2026, 7, 16, 1),
        utc(2026, 7, 17, 1),
        utc(2026, 7, 20, 1),
    )


def test_cron_day_and_weekday_follow_standard_or_semantics() -> None:
    schedule = CronSchedule(expression="0 9 1 * MON", timezone="UTC")

    assert preview_occurrences(schedule, after=utc(2026, 6, 30), count=3) == (
        utc(2026, 7, 1, 9),
        utc(2026, 7, 6, 9),
        utc(2026, 7, 13, 9),
    )


def test_cron_skips_nonexistent_dst_wall_time() -> None:
    schedule = CronSchedule(expression="30 2 * * *", timezone="America/New_York")

    # 2026-03-08 02:30 does not exist when clocks spring forward.
    assert preview_occurrences(schedule, after=utc(2026, 3, 7, 8), count=2) == (
        utc(2026, 3, 9, 6, 30),
        utc(2026, 3, 10, 6, 30),
    )


def test_cron_runs_only_first_repeated_dst_wall_time() -> None:
    schedule = CronSchedule(expression="30 1 * * *", timezone="America/New_York")

    # 2026-11-01 01:30 occurs twice; only fold=0 is scheduled.
    assert preview_occurrences(schedule, after=utc(2026, 10, 31, 6), count=2) == (
        utc(2026, 11, 1, 5, 30),
        utc(2026, 11, 2, 6, 30),
    )


def test_start_and_end_boundaries_are_inclusive() -> None:
    schedule = IntervalSchedule(seconds=3600, anchor_at=utc(2026, 7, 16), timezone="UTC")

    assert preview_occurrences(
        schedule,
        after=utc(2026, 7, 16),
        start_at=utc(2026, 7, 16, 2),
        end_at=utc(2026, 7, 16, 4),
    ) == (utc(2026, 7, 16, 2), utc(2026, 7, 16, 3), utc(2026, 7, 16, 4))


def test_fire_once_misfire_emits_one_and_skips_historical_backfill() -> None:
    schedule = IntervalSchedule(seconds=3600, anchor_at=utc(2026, 7, 16), timezone="UTC")

    resolution = resolve_due(
        schedule,
        scheduled_for=utc(2026, 7, 16, 1),
        now=utc(2026, 7, 16, 5, 30),
        policy=MisfirePolicy.FIRE_ONCE,
    )

    assert resolution.occurrence_at == utc(2026, 7, 16, 1)
    assert resolution.next_trigger_at == utc(2026, 7, 16, 6)
    assert resolution.misfired is True


def test_skip_misfire_drops_occurrence_and_advances_to_future() -> None:
    schedule = CronSchedule(expression="0 * * * *", timezone="UTC")

    resolution = resolve_due(
        schedule,
        scheduled_for=utc(2026, 7, 16, 1),
        now=utc(2026, 7, 16, 5, 30),
        policy=MisfirePolicy.SKIP,
    )

    assert resolution.occurrence_at is None
    assert resolution.next_trigger_at == utc(2026, 7, 16, 6)
    assert resolution.misfired is True


def test_on_time_due_executes_even_with_skip_policy() -> None:
    schedule = CronSchedule(expression="0 * * * *", timezone="UTC")

    resolution = resolve_due(
        schedule,
        scheduled_for=utc(2026, 7, 16, 5),
        now=utc(2026, 7, 16, 5),
        policy=MisfirePolicy.SKIP,
    )

    assert resolution.occurrence_at == utc(2026, 7, 16, 5)
    assert resolution.next_trigger_at == utc(2026, 7, 16, 6)
    assert resolution.misfired is False


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: CronSchedule(expression="* * * *", timezone="UTC"), "exactly five"),
        (lambda: CronSchedule(expression="60 * * * *", timezone="UTC"), "out of range"),
        (
            lambda: IntervalSchedule(seconds=59, anchor_at=utc(2026, 7, 16), timezone="UTC"),
            "at least 60",
        ),
        (
            lambda: OnceSchedule(run_at=utc(2026, 7, 16), timezone="Mars/Olympus"),
            "unknown timezone",
        ),
        (
            lambda: OnceSchedule(run_at=datetime(2026, 7, 16), timezone="UTC"),
            "must include a timezone",
        ),
    ],
)
def test_invalid_schedule_is_rejected(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ScheduleValidationError, match=message):
        factory()


def test_configurable_frequency_guard_rejects_excessive_cron() -> None:
    schedule = CronSchedule(expression="* * * * *", timezone="UTC")

    with pytest.raises(ScheduleValidationError, match="at least 300 seconds"):
        validate_minimum_frequency(
            schedule,
            after=utc(2026, 7, 16),
            minimum_seconds=300,
        )


def test_creation_validation_rejects_past_once_schedule() -> None:
    schedule = OnceSchedule(run_at=utc(2026, 7, 16), timezone="UTC")

    with pytest.raises(ScheduleValidationError, match="future"):
        validate_schedule(schedule, now=utc(2026, 7, 17))


def test_cron_step_from_explicit_value_extends_to_field_maximum() -> None:
    schedule = CronSchedule(expression="5/10 * * * *", timezone="UTC")

    assert preview_occurrences(schedule, after=utc(2026, 7, 16), count=3) == (
        datetime(2026, 7, 16, 0, 5, tzinfo=UTC),
        datetime(2026, 7, 16, 0, 15, tzinfo=UTC),
        datetime(2026, 7, 16, 0, 25, tzinfo=UTC),
    )


def test_end_before_start_is_rejected() -> None:
    schedule = CronSchedule(expression="0 9 * * *", timezone="UTC")

    with pytest.raises(ScheduleValidationError, match="start_at"):
        next_occurrence(
            schedule,
            after=utc(2026, 7, 16),
            start_at=utc(2026, 7, 18),
            end_at=utc(2026, 7, 17),
        )
