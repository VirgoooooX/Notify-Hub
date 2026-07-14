from datetime import UTC, datetime

from app.application.conversation_service import parse_reminder_text


def test_parse_explicit_chinese_reminder_time() -> None:
    draft = parse_reminder_text(
        "明天下午三点提醒我交电费",
        now=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
    )
    assert draft.scheduled_at == datetime(2026, 7, 15, 7, 0, tzinfo=UTC)
    assert not draft.ambiguous
    assert "交电费" in draft.title


def test_ambiguous_time_requires_follow_up() -> None:
    draft = parse_reminder_text(
        "明天三点提醒我开会",
        now=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
    )
    assert draft.ambiguous
