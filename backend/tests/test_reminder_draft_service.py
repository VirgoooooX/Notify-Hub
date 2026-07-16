from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from app.application.reminder_draft_service import (
    ReminderDraftCreate,
    ReminderDraftService,
    ReminderDraftUpdate,
)
from app.application.reminder_service import EventAcceptance, ReminderCreate, ReminderService
from app.domain.reminder_drafts import (
    ReminderDraftExpired,
    ReminderDraftParseMethod,
    ReminderDraftSourceType,
    ReminderDraftStatus,
)
from app.domain.reminders import AckPolicy, ScheduleType
from app.infrastructure.database import Base
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Person
from app.infrastructure.database.reminder_draft_models import ReminderDraft
from app.infrastructure.database.reminder_models import Reminder
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class RecordingReminderCreator:
    def __init__(self) -> None:
        self.commands: list[ReminderCreate] = []

    async def create(self, command: ReminderCreate, *, now: datetime | None = None) -> Reminder:
        self.commands.append(command)
        instant = now or datetime.now(UTC)
        return Reminder(
            id=new_id("rem"),
            creator_person_id=command.creator_person_id,
            title=command.title,
            content=command.content,
            content_type=command.content_type,
            media_asset_id=None,
            url=None,
            schedule_type=command.schedule_type.value,
            scheduled_at=command.scheduled_at,
            recurrence_rule=command.recurrence_rule,
            timezone=command.timezone,
            next_run_at=command.scheduled_at,
            status="active",
            require_ack=False,
            ack_policy="any",
            repeat_interval_seconds=None,
            max_reminders=None,
            reminder_count=0,
            stop_at=None,
            escalation_stop_after_seconds=None,
            claim_expires_at=None,
            claimed_by=None,
            created_at=instant,
            updated_at=instant,
        )


@pytest.fixture
async def draft_service(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'drafts.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    async with factory() as session, session.begin():
        session.add(
            Person(
                id="per_owner",
                display_name="Owner",
                active=True,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
        )
    creator = RecordingReminderCreator()
    service = ReminderDraftService(factory, creator)
    try:
        yield service, creator, factory, now
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unconfirmed_and_ai_results_only_create_drafts(draft_service) -> None:
    service, creator, factory, now = draft_service
    draft = await service.create(
        ReminderDraftCreate(
            source_type=ReminderDraftSourceType.WECOM_TEXT,
            source_text="明天八点提醒我带药",
            parsed_data={"title": "带药"},
            parse_method=ReminderDraftParseMethod.AI,
            validation_errors=(),
            created_by="per_owner",
            status=ReminderDraftStatus.AWAITING_CONFIRMATION,
        ),
        now=now,
    )

    assert creator.commands == []
    async with factory() as session:
        stored = cast(ReminderDraft, await session.get(ReminderDraft, draft.id))
        assert stored.parse_method == "ai"
        assert stored.status == "awaiting_confirmation"
        assert await session.get(Reminder, draft.id) is None


@pytest.mark.asyncio
async def test_confirm_calls_reminder_service_and_marks_draft(draft_service) -> None:
    _, _, factory, now = draft_service

    async def unused_emit(_draft) -> EventAcceptance:
        raise AssertionError("creating a reminder must not emit before it is due")

    reminders = ReminderService(factory, unused_emit, "draft-test-secret")
    service = ReminderDraftService(factory, reminders)
    draft = await service.create(
        ReminderDraftCreate(
            source_type=ReminderDraftSourceType.WECOM_TEXT,
            source_text="半小时后提醒我喝水",
            parsed_data={"title": "喝水"},
            parse_method=ReminderDraftParseMethod.RULES,
            validation_errors=(),
            created_by="per_owner",
            status=ReminderDraftStatus.AWAITING_CONFIRMATION,
        ),
        now=now,
    )
    command = ReminderCreate(
        creator_person_id="per_owner",
        title="喝水",
        content="喝水",
        schedule_type=ScheduleType.ONCE,
        timezone="Asia/Shanghai",
        recipient_ids=("per_owner",),
        scheduled_at=now + timedelta(minutes=30),
        ack_policy=AckPolicy.ANY,
    )

    reminder = await service.confirm(draft.id, command, created_by="per_owner", now=now)

    async with factory() as session:
        stored = cast(ReminderDraft, await session.get(ReminderDraft, draft.id))
        assert stored.status == "confirmed"
        assert stored.confirmed_reminder_id == reminder.id
        assert cast(Reminder, await session.get(Reminder, reminder.id)).title == "喝水"


@pytest.mark.asyncio
async def test_expired_draft_cannot_be_confirmed_and_cleanup_is_persistent(draft_service) -> None:
    service, creator, factory, now = draft_service
    draft = await service.create(
        ReminderDraftCreate(
            source_type=ReminderDraftSourceType.WECOM_TEXT,
            source_text="明天提醒我",
            parsed_data={},
            parse_method=ReminderDraftParseMethod.RULES,
            validation_errors=("time is ambiguous",),
            created_by="per_owner",
            expires_at=now + timedelta(minutes=1),
        ),
        now=now,
    )
    await service.update(
        draft.id,
        ReminderDraftUpdate(validation_errors=(), status=ReminderDraftStatus.AWAITING_CONFIRMATION),
        created_by="per_owner",
        now=now,
    )

    with pytest.raises(ReminderDraftExpired):
        await service.get_for_confirmation(
            draft.id,
            created_by="per_owner",
            now=now + timedelta(minutes=2),
        )

    assert creator.commands == []
    async with factory() as session:
        stored = cast(ReminderDraft, await session.get(ReminderDraft, draft.id))
        assert stored.status == "expired"


@pytest.mark.asyncio
async def test_expire_due_only_expires_open_drafts(draft_service) -> None:
    service, _, factory, now = draft_service
    first = await service.create(
        ReminderDraftCreate(
            source_type=ReminderDraftSourceType.WEB,
            source_text="",
            parsed_data={},
            parse_method=ReminderDraftParseMethod.MANUAL,
            validation_errors=(),
            created_by="per_owner",
            expires_at=now + timedelta(minutes=1),
        ),
        now=now,
    )
    second = await service.create(
        ReminderDraftCreate(
            source_type=ReminderDraftSourceType.WEB,
            source_text="",
            parsed_data={},
            parse_method=ReminderDraftParseMethod.MANUAL,
            validation_errors=(),
            created_by="per_owner",
            expires_at=now + timedelta(minutes=1),
        ),
        now=now,
    )
    await service.cancel(second.id, created_by="per_owner", now=now)

    assert await service.expire_due(now=now + timedelta(minutes=2)) == 1
    async with factory() as session:
        assert cast(ReminderDraft, await session.get(ReminderDraft, first.id)).status == "expired"
        assert (
            cast(ReminderDraft, await session.get(ReminderDraft, second.id)).status == "cancelled"
        )
