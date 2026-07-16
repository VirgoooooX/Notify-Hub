from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.conversation_service import ConversationService
from app.application.reminder_service import ReminderCreate, ReminderService
from app.application.runtime_adapters import ReminderEventEmitterAdapter
from app.domain.reminders import ScheduleType
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.models import Delivery, Notification, Person, WeComIdentity
from app.infrastructure.database.reminder_models import (
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from sqlalchemy import select


class MockAIService:
    def __init__(self, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.calls = []

    async def extract(self, **kwargs: Any):
        self.calls.append(kwargs)
        from app.ai.schemas import AIExtractionResult

        if self.should_succeed:
            return AIExtractionResult(
                values={
                    "title": "Meeting",
                    "content": "Sync tomorrow morning",
                    "scheduled_at": "2026-07-17T09:00:00+08:00",
                    "recurrence_rule": None,
                },
                confidence=0.95,
                reason="Parsed text successfully",
            )
        else:
            raise RuntimeError("AI extraction failed")


@pytest.mark.asyncio
async def test_reminder_media_and_url_creation(api: tuple[Any, Any]) -> None:
    _client, app = api
    service = app.state.reminder_service
    async with app.state.session_factory() as session:
        person = Person(
            id="usr_bob",
            display_name="Bob",
            active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add_all(
            [
                person,
                MediaAsset(
                    id="med_reminder_image",
                    kind="image",
                    mime_type="image/png",
                    storage_path="test/reminder-image.png",
                    checksum_sha256="0" * 64,
                    size_bytes=4,
                    duration_seconds=None,
                    source="upload",
                    created_by="usr_bob",
                    created_at=datetime.now(UTC),
                    expires_at=None,
                    provider_media_id=None,
                    provider_expires_at=None,
                ),
            ]
        )
        await session.commit()

    start = datetime.now(UTC)
    reminder = await service.create(
        ReminderCreate(
            creator_person_id="usr_bob",
            title="Image reminder",
            content="Check image",
            content_type="image",
            media_asset_id="med_reminder_image",
            url="https://example.com/image.png",
            schedule_type=ScheduleType.ONCE,
            timezone="UTC",
            recipient_ids=("usr_bob",),
            scheduled_at=start,
            require_ack=False,
        ),
        now=start,
    )

    assert reminder.content_type == "image"
    assert reminder.url == "https://example.com/image.png"

    assert await service.claim_due(worker_id="test-worker", now=start) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="test-worker", now=start)
    claimed_recipients = await service.claim_due_recipients(worker_id="test-worker", now=start)
    assert len(claimed_recipients) == 1
    await service.notify_recipient(claimed_recipients[0], worker_id="test-worker", now=start)

    async with app.state.session_factory() as session:
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder.id)
        )
        assert occurrence is not None
        assert occurrence.content_type_snapshot == "image"
        assert occurrence.url == "https://example.com/image.png"
        notification = await session.scalar(
            select(Notification).where(Notification.reminder_occurrence_id == occurrence.id)
        )
        assert notification is not None
        assert notification.media_asset_id == "med_reminder_image"
        assert notification.url == "https://example.com/image.png"
        delivery = await session.scalar(
            select(Delivery).where(Delivery.notification_id == notification.id)
        )
        assert delivery is not None
        assert delivery.status == "pending"
        recipient = await session.scalar(
            select(ReminderOccurrenceRecipient).where(
                ReminderOccurrenceRecipient.occurrence_id == occurrence.id
            )
        )
        assert recipient is not None
        assert recipient.notify_count == 1


@pytest.mark.asyncio
async def test_conversation_ai_fallback(api: tuple[Any, Any]) -> None:
    _client, app = api
    service = app.state.reminder_service
    async with app.state.session_factory() as session:
        person = Person(
            id="usr_charles",
            display_name="Charles",
            active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(person)
        session.add(
            WeComIdentity(
                id="wci_charles",
                person_id="usr_charles",
                user_id="charles_userid",
                active=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.flush()

        from app.infrastructure.database.ai_models import AIProfile, AIProvider

        provider = AIProvider(
            id="aip_mock",
            name="Mock",
            preset="custom",
            protocol="openai_chat_completions",
            base_url="https://mock.example/v1",
            enabled=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(provider)
        await session.flush()  # Flush the provider first so its ID is in database for the FK

        profile = AIProfile(
            id="aip_profile_mock",
            name="Mock Profile",
            capability="extract",
            provider_id="aip_mock",
            model="gpt-4",
            enabled=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(profile)
        await session.commit()

    mock_ai = MockAIService(should_succeed=True)
    conv_service = ConversationService(
        app.state.session_factory,
        service,
        ai=mock_ai,
        ai_profile_id="aip_profile_mock",
        default_timezone="Asia/Shanghai",
    )

    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    reply = await conv_service.handle_text(
        sender_wecom_userid="charles_userid", text="明天早上提醒我Meeting", now=now
    )

    assert len(mock_ai.calls) == 1
    assert reply.code == "draft"
    assert "Meeting" in reply.text


@pytest.mark.asyncio
async def test_conversation_rejects_disabled_person(api: tuple[Any, Any]) -> None:
    _client, app = api
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Person(
                id="usr_disabled_conversation",
                display_name="Disabled",
                active=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WeComIdentity(
                id="wci_disabled_conversation",
                person_id="usr_disabled_conversation",
                user_id="disabled_conversation_userid",
                active=True,
                created_at=now,
                updated_at=now,
            )
        )

    reply = await app.state.conversation_service.handle_text(
        sender_wecom_userid="disabled_conversation_userid",
        text="明天下午三点提醒我开会",
        now=now,
    )

    assert reply.code == "forbidden"


@pytest.mark.asyncio
async def test_emit_failure_does_not_consume_occurrence_attempt(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Person(
                id="usr_retry",
                display_name="Retry",
                active=True,
                created_at=now,
                updated_at=now,
            )
        )

    delegate = ReminderEventEmitterAdapter(app.state.event_service)
    should_fail = True

    async def flaky_emit(draft: Any) -> Any:
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise RuntimeError("temporary event failure")
        return await delegate(draft)

    service = ReminderService(app.state.session_factory, flaky_emit, "retry-secret")
    reminder = await service.create(
        ReminderCreate(
            creator_person_id="usr_retry",
            title="Retry safely",
            content="",
            schedule_type=ScheduleType.ONCE,
            timezone="UTC",
            recipient_ids=("usr_retry",),
            scheduled_at=now,
        ),
        now=now,
    )
    assert await service.claim_due(worker_id="retry-worker", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="retry-worker", now=now)
    claimed = await service.claim_due_recipients(worker_id="retry-worker", now=now)
    with pytest.raises(RuntimeError, match="temporary event failure"):
        await service.notify_recipient(claimed[0], worker_id="retry-worker", now=now)

    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, claimed[0])
        assert recipient is not None
        assert recipient.notify_count == 0
        assert recipient.claimed_by is None

    claimed_again = await service.claim_due_recipients(worker_id="retry-worker", now=now)
    assert claimed_again == claimed
    await service.notify_recipient(claimed_again[0], worker_id="retry-worker", now=now)

    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, claimed[0])
        assert recipient is not None
        assert recipient.notify_count == 1
        assert recipient.status == "acknowledged"
