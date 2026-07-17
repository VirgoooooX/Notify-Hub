from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.application.reminder_service import ReminderCreate, ReminderService
from app.application.runtime_adapters import ReminderEventEmitterAdapter
from app.channels.base import FakeChannel
from app.domain.reminders import AckPolicy, ScheduleType
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.models import Delivery, Event, Notification, Person, WeComIdentity
from app.infrastructure.database.reminder_models import (
    IncomingMessage,
    InteractionEvent,
    Reminder,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from app.workers.delivery_worker import DeliveryWorker
from app.workers.media_cleanup_worker import MediaCleanupWorker
from app.workers.reminder_worker import ReminderWorker
from sqlalchemy import func, select, update

from tests.test_core_api import initialize_and_login


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dead_interaction_and_failed_message_can_be_replayed(
    api: tuple[Any, Any],
) -> None:
    client, app = api
    access = await initialize_and_login(client)
    headers = {"Authorization": f"Bearer {access}"}
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add_all(
            [
                InteractionEvent(
                    id="int_dead_retry",
                    channel="wecom",
                    dedupe_key="dead-retry",
                    sender_external_id="user-dead",
                    notification_action_id=None,
                    action="complete",
                    status="dead",
                    result=None,
                    response_code=None,
                    attempt_count=5,
                    max_attempts=5,
                    last_error="RuntimeError",
                    claimed_by=None,
                    claim_expires_at=None,
                    received_at=now,
                    processed_at=now,
                ),
                IncomingMessage(
                    id="msg_failed_retry",
                    channel="wecom",
                    sender_external_id="user-failed",
                    provider_message_id="provider-failed",
                    dedupe_key="failed-retry",
                    message_type="text",
                    text="提醒我",
                    media_refs={},
                    event_payload={},
                    received_at=now,
                    processed_at=now,
                    processing_status="failed",
                    error_message="RuntimeError",
                ),
            ]
        )

    metrics = await client.get("/api/v1/admin/reminders/metrics", headers=headers)
    assert metrics.status_code == 200
    assert metrics.json()["data"]["dead_interactions"] == 1
    assert metrics.json()["data"]["failed_messages"] == 1
    assert (
        await client.post(
            "/api/v1/admin/reminders/interactions/int_dead_retry/retry", headers=headers
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/admin/reminders/messages/msg_failed_retry/retry", headers=headers
        )
    ).status_code == 200
    async with app.state.session_factory() as session:
        interaction = await session.get(InteractionEvent, "int_dead_retry")
        message = await session.get(IncomingMessage, "msg_failed_retry")
        assert interaction is not None and interaction.status == "pending"
        assert interaction.attempt_count == 0
        assert message is not None and message.processing_status == "pending"


def _stored_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata even for DateTime(timezone=True)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class _FixedClock:
    def __init__(self, current: datetime) -> None:
        self._current = current

    def now(self) -> datetime:
        return self._current


async def _add_person(app: Any, person_id: str, *, wecom_user_id: str | None = None) -> None:
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Person(
                id=person_id,
                display_name=person_id,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        if wecom_user_id is not None:
            session.add(
                WeComIdentity(
                    id=f"wci_{person_id}",
                    person_id=person_id,
                    user_id=wecom_user_id,
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )


async def _create_due_reminder(
    service: ReminderService,
    *,
    person_id: str,
    now: datetime,
    require_ack: bool = False,
    schedule_type: ScheduleType = ScheduleType.ONCE,
    recurrence_rule: str | None = None,
    media_asset_id: str | None = None,
    scheduled_at: datetime | None = None,
) -> Reminder:
    return await service.create(
        ReminderCreate(
            creator_person_id=person_id,
            title="Reliable reminder",
            content="Do the thing",
            content_type="image" if media_asset_id else "text",
            media_asset_id=media_asset_id,
            schedule_type=schedule_type,
            timezone="UTC",
            recipient_ids=(person_id,),
            scheduled_at=scheduled_at or now,
            recurrence_rule=recurrence_rule,
            require_ack=require_ack,
            ack_policy=AckPolicy.ANY,
            repeat_interval_seconds=300 if require_ack else None,
            max_reminders=3 if require_ack else None,
            stop_at=now + timedelta(hours=1) if require_ack else None,
        ),
        now=now,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_planner_repeated_scan_is_idempotent(api: tuple[Any, Any]) -> None:
    _client, app = api
    now = datetime.now(UTC)
    await _add_person(app, "person_planner")
    reminder = await _create_due_reminder(
        app.state.reminder_service,
        person_id="person_planner",
        now=now,
        require_ack=True,
    )
    worker = ReminderWorker(app.state.reminder_service, worker_id="planner-idempotency")

    assert await worker.run_once(now=now) == 1
    assert await worker.run_once(now=now) == 0

    async with app.state.session_factory() as session:
        occurrence_count = await session.scalar(
            select(func.count(ReminderOccurrence.id)).where(
                ReminderOccurrence.reminder_id == reminder.id
            )
        )
        event_count = await session.scalar(
            select(func.count(Event.id)).where(
                Event.source_type == "reminder",
                Event.source_id == reminder.id,
            )
        )
        notification_count = await session.scalar(
            select(func.count(Notification.id)).where(Notification.reminder_id == reminder.id)
        )
        delivery_count = await session.scalar(
            select(func.count(Delivery.id))
            .join(Notification, Notification.id == Delivery.notification_id)
            .where(Notification.reminder_id == reminder.id)
        )
    assert occurrence_count == 1
    assert event_count == 1
    assert notification_count == 1
    assert delivery_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_once_non_ack_reminder_is_delivered_exactly_once(api: tuple[Any, Any]) -> None:
    _client, app = api
    now = datetime.now(UTC)
    await _add_person(app, "person_once_delivery", wecom_user_id="once-delivery-user")
    reminder = await _create_due_reminder(
        app.state.reminder_service,
        person_id="person_once_delivery",
        now=now,
    )
    reminder_worker = ReminderWorker(app.state.reminder_service, worker_id="once-delivery-planner")
    assert await reminder_worker.run_once(now=now) == 1

    channel = FakeChannel()
    delivery_worker = DeliveryWorker(
        app.state.session_factory,
        channel,
        app.state.clock,
        "once-delivery-worker",
    )
    assert await delivery_worker.process_one()
    assert len(channel.messages) == 1
    assert channel.messages[0].recipients == ["once-delivery-user"]

    assert await reminder_worker.run_once(now=now + timedelta(seconds=1)) == 0
    assert not await delivery_worker.process_one()
    async with app.state.session_factory() as session:
        delivery_count = await session.scalar(
            select(func.count(Delivery.id))
            .join(Notification, Notification.id == Delivery.notification_id)
            .where(Notification.reminder_id == reminder.id)
        )
    assert delivery_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_escalation_emit_failure_preserves_attempt_and_is_retryable(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    await _add_person(app, "person_emit_retry")
    delegate = ReminderEventEmitterAdapter(app.state.event_service)
    fail_once = True

    async def flaky_emit(draft: Any) -> Any:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("temporary event-store failure")
        return await delegate(draft)

    service = ReminderService(app.state.session_factory, flaky_emit, "reliability-secret")
    reminder = await _create_due_reminder(
        service,
        person_id="person_emit_retry",
        now=now,
        require_ack=True,
    )
    assert await service.claim_due(worker_id="retry-worker", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="retry-worker", now=now)
    recipient_ids = await service.claim_due_recipients(worker_id="retry-worker", now=now)
    assert len(recipient_ids) == 1

    with pytest.raises(RuntimeError, match="temporary event-store failure"):
        await service.notify_recipient(recipient_ids[0], worker_id="retry-worker", now=now)

    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, recipient_ids[0])
        assert recipient is not None
        assert recipient.notify_count == 0
        assert recipient.last_notified_at is None
        assert recipient.next_notify_at is not None
        assert _stored_utc(recipient.next_notify_at) == now
        assert recipient.claimed_by is None
        assert recipient.claim_expires_at is None
        assert recipient.status == "pending"

    assert await service.claim_due_recipients(worker_id="retry-worker", now=now) == recipient_ids
    acceptance = await service.notify_recipient(recipient_ids[0], worker_id="retry-worker", now=now)
    assert acceptance is not None and acceptance.accepted

    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, recipient_ids[0])
        assert recipient is not None
        assert recipient.notify_count == 1
        assert recipient.next_notify_at is not None
        assert _stored_utc(recipient.next_notify_at) == now + timedelta(minutes=5)
        assert recipient.status == "pending"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_continuous_reminder_stops_after_completion(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC).replace(microsecond=0)
    person_id = "person_daily_complete"
    await _add_person(app, person_id, wecom_user_id="daily-complete-user")
    service = app.state.reminder_service
    reminder = await service.create(
        ReminderCreate(
            creator_person_id=person_id,
            title="Daily parking fee reminder",
            content="Pay the parking fee",
            schedule_type=ScheduleType.ONCE,
            timezone="UTC",
            recipient_ids=(person_id,),
            scheduled_at=now,
            require_ack=True,
            ack_policy=AckPolicy.ANY,
            repeat_interval_seconds=86_400,
            max_reminders=3,
        ),
        now=now,
    )
    assert _stored_utc(reminder.stop_at) == now + timedelta(days=3)
    assert reminder.escalation_stop_after_seconds == 3 * 86_400

    assert await service.claim_due(worker_id="daily-planner", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="daily-planner", now=now)
    async with app.state.session_factory() as session:
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder.id)
        )
    assert occurrence is not None
    delivery_worker = DeliveryWorker(
        app.state.session_factory,
        FakeChannel(),
        app.state.clock,
        "daily-delivery-worker",
    )

    first_due = await service.claim_due_recipients(worker_id="daily-first", now=now)
    assert len(first_due) == 1
    assert await service.notify_recipient(first_due[0], worker_id="daily-first", now=now)
    assert await delivery_worker.process_one()

    second_at = now + timedelta(days=1)
    second_due = await service.claim_due_recipients(worker_id="daily-second", now=second_at)
    assert second_due == first_due
    assert await service.notify_recipient(second_due[0], worker_id="daily-second", now=second_at)
    assert await delivery_worker.process_one()

    result = await service.operate_latest_interactive(
        sender_wecom_userid="daily-complete-user",
        operation="complete",
        now=second_at + timedelta(minutes=1),
    )
    assert result.code == "completed"
    assert (
        await service.claim_due_recipients(worker_id="daily-third", now=now + timedelta(days=2))
        == []
    )

    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, first_due[0])
        stored_occurrence = await session.get(ReminderOccurrence, occurrence.id)
        event_count = await session.scalar(
            select(func.count(Event.id)).where(Event.source_id == reminder.id)
        )
    assert recipient is not None
    assert recipient.status == "acknowledged"
    assert recipient.notify_count == 2
    assert recipient.next_notify_at is None
    assert stored_occurrence is not None and stored_occurrence.status == "acknowledged"
    assert event_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_continuous_reminder_sends_three_times_without_completion(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC).replace(microsecond=0)
    person_id = "person_daily_exhausted"
    await _add_person(app, person_id)
    service = app.state.reminder_service
    reminder = await service.create(
        ReminderCreate(
            creator_person_id=person_id,
            title="Daily parking fee reminder",
            content="Pay the parking fee",
            schedule_type=ScheduleType.ONCE,
            timezone="UTC",
            recipient_ids=(person_id,),
            scheduled_at=now,
            require_ack=True,
            ack_policy=AckPolicy.ANY,
            repeat_interval_seconds=86_400,
            max_reminders=3,
        ),
        now=now,
    )
    assert await service.claim_due(worker_id="daily-planner", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="daily-planner", now=now)

    recipient_id: str | None = None
    for index in range(3):
        instant = now + timedelta(days=index)
        claimed = await service.claim_due_recipients(worker_id=f"daily-send-{index}", now=instant)
        assert len(claimed) == 1
        recipient_id = claimed[0]
        acceptance = await service.notify_recipient(
            recipient_id, worker_id=f"daily-send-{index}", now=instant
        )
        assert acceptance is not None and acceptance.accepted

    assert recipient_id is not None
    final_claim = await service.claim_due_recipients(
        worker_id="daily-fourth", now=now + timedelta(days=3)
    )
    assert final_claim == [recipient_id]
    assert (
        await service.notify_recipient(
            recipient_id, worker_id="daily-fourth", now=now + timedelta(days=3)
        )
        is None
    )

    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, recipient_id)
        event_count = await session.scalar(
            select(func.count(Event.id)).where(Event.source_id == reminder.id)
        )
    assert recipient is not None
    assert recipient.status == "expired"
    assert recipient.notify_count == 3
    assert recipient.next_notify_at is None
    assert event_count == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recipient_claim_is_recovered_only_after_lease_expires(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    await _add_person(app, "person_lease")
    service = app.state.reminder_service
    reminder = await _create_due_reminder(
        service,
        person_id="person_lease",
        now=now,
        require_ack=True,
    )
    assert await service.claim_due(worker_id="planner", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="planner", now=now)

    first_claim = await service.claim_due_recipients(
        worker_id="crashed-worker", now=now, lease_seconds=30
    )
    assert len(first_claim) == 1
    assert (
        await service.claim_due_recipients(worker_id="replacement", now=now + timedelta(seconds=29))
        == []
    )
    assert (
        await service.claim_due_recipients(worker_id="replacement", now=now + timedelta(seconds=31))
        == first_claim
    )

    acceptance = await service.notify_recipient(
        first_claim[0], worker_id="replacement", now=now + timedelta(seconds=31)
    )
    assert acceptance is not None and acceptance.accepted


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_restart_recovers_expired_planner_claim_without_duplicate(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    await _add_person(app, "person_worker_restart")
    first_service = app.state.reminder_service
    reminder = await _create_due_reminder(
        first_service,
        person_id="person_worker_restart",
        now=now,
        require_ack=True,
    )
    assert await first_service.claim_due(
        worker_id="crashed-planner", now=now, lease_seconds=30
    ) == [reminder.id]

    restarted_service = ReminderService(
        app.state.session_factory,
        ReminderEventEmitterAdapter(app.state.event_service),
        "restart-secret",
    )
    restarted_worker = ReminderWorker(restarted_service, worker_id="restarted-planner")
    assert await restarted_worker.run_once(now=now + timedelta(seconds=29)) == 0
    assert await restarted_worker.run_once(now=now + timedelta(seconds=31)) == 1
    assert await restarted_worker.run_once(now=now + timedelta(seconds=32)) == 0

    async with app.state.session_factory() as session:
        occurrence_count = await session.scalar(
            select(func.count(ReminderOccurrence.id)).where(
                ReminderOccurrence.reminder_id == reminder.id
            )
        )
        event_count = await session.scalar(
            select(func.count(Event.id)).where(Event.source_id == reminder.id)
        )
        delivery_count = await session.scalar(
            select(func.count(Delivery.id))
            .join(Notification, Notification.id == Delivery.notification_id)
            .where(Notification.reminder_id == reminder.id)
        )
    assert occurrence_count == 1
    assert event_count == 1
    assert delivery_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_acknowledging_one_occurrence_does_not_cancel_another_occurrence_delivery(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    first_run = datetime.now(UTC).replace(microsecond=0)
    await _add_person(app, "person_occurrence_scope", wecom_user_id="scope-user")
    service = app.state.reminder_service
    reminder = await _create_due_reminder(
        service,
        person_id="person_occurrence_scope",
        now=first_run,
        require_ack=True,
        schedule_type=ScheduleType.RECURRING,
        recurrence_rule="FREQ=MINUTELY;COUNT=3",
    )

    for index, instant in enumerate((first_run, first_run + timedelta(minutes=1)), start=1):
        worker_id = f"planner-{index}"
        assert await service.claim_due(worker_id=worker_id, now=instant) == [reminder.id]
        await service.trigger_claimed(reminder.id, worker_id=worker_id, now=instant)
        due = await service.claim_due_recipients(worker_id=worker_id, now=instant)
        assert len(due) == 1
        acceptance = await service.notify_recipient(due[0], worker_id=worker_id, now=instant)
        assert acceptance is not None and acceptance.accepted

    async with app.state.session_factory() as session:
        occurrences = list(
            await session.scalars(
                select(ReminderOccurrence)
                .where(ReminderOccurrence.reminder_id == reminder.id)
                .order_by(ReminderOccurrence.scheduled_for)
            )
        )
        assert len(occurrences) == 2
    delivery_worker = DeliveryWorker(
        app.state.session_factory,
        FakeChannel(),
        app.state.clock,
        "scope-delivery-worker",
    )
    assert await delivery_worker.process_one()
    result = await service.operate_latest_interactive(
        sender_wecom_userid="scope-user",
        operation="complete",
        now=first_run + timedelta(minutes=1, seconds=1),
    )
    assert result.code == "completed"

    async with app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(Notification.reminder_occurrence_id, Delivery.status)
                .join(Delivery, Delivery.notification_id == Notification.id)
                .where(Notification.reminder_occurrence_id.in_([item.id for item in occurrences]))
            )
        ).all()
        statuses = {occurrence_id: status for occurrence_id, status in rows}
        current_reminder = await session.get(Reminder, reminder.id)
    assert statuses[occurrences[0].id] == "succeeded"
    assert statuses[occurrences[1].id] == "pending"
    assert current_reminder is not None and current_reminder.status == "active"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_media_delete_conflicts_with_definition_and_occurrence_snapshot(
    api: tuple[Any, Any],
) -> None:
    client, app = api
    token = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {token}"}
    now = datetime.now(UTC)
    await _add_person(app, "person_media_reference")
    asset_id = "med_reliability_reference"
    async with app.state.session_factory() as session, session.begin():
        session.add(
            MediaAsset(
                id=asset_id,
                kind="image",
                mime_type="image/png",
                storage_path="tests/reliability-reference.png",
                checksum_sha256="a" * 64,
                size_bytes=8,
                duration_seconds=None,
                source="upload",
                created_by="person_media_reference",
                created_at=now,
                expires_at=None,
                provider_media_id=None,
                provider_expires_at=None,
            )
        )
    service = app.state.reminder_service
    reminder = await _create_due_reminder(
        service,
        person_id="person_media_reference",
        now=now,
        media_asset_id=asset_id,
    )

    response = await client.delete(f"/api/v1/admin/media/{asset_id}", headers=auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "media_in_use"

    assert await service.claim_due(worker_id="media-planner", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="media-planner", now=now)
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(Reminder).where(Reminder.id == reminder.id).values(media_asset_id=None)
        )

    response = await client.delete(f"/api/v1/admin/media/{asset_id}", headers=auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "media_in_use"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_media_cleanup_skips_references_and_honors_orphan_batch_size(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC).replace(microsecond=0)
    await _add_person(app, "person_media_cleanup")
    storage = app.state.media_service.storage
    asset_ids = (
        "med_cleanup_definition",
        "med_cleanup_snapshot",
        "med_cleanup_orphan_1",
        "med_cleanup_orphan_2",
        "med_cleanup_orphan_3",
    )
    stored_paths: dict[str, Path] = {}
    async with app.state.session_factory() as session, session.begin():
        ordered_assets = zip(asset_ids, (5, 4, 3, 2, 1), strict=True)
        for index, (asset_id, minutes_ago) in enumerate(ordered_assets, start=1):
            relative_path = f"reliability/{asset_id}.png"
            stored_path = storage.resolve(relative_path)
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            stored_path.write_bytes(b"referenced-or-orphan")
            stored_paths[asset_id] = stored_path
            session.add(
                MediaAsset(
                    id=asset_id,
                    kind="image",
                    mime_type="image/png",
                    storage_path=relative_path,
                    checksum_sha256=f"{index:064x}",
                    size_bytes=20,
                    duration_seconds=None,
                    source="test",
                    created_by="person_media_cleanup",
                    created_at=now - timedelta(minutes=minutes_ago + 1),
                    expires_at=now - timedelta(minutes=minutes_ago),
                    provider_media_id=None,
                    provider_expires_at=None,
                )
            )

    await _create_due_reminder(
        app.state.reminder_service,
        person_id="person_media_cleanup",
        now=now,
        media_asset_id=asset_ids[0],
        scheduled_at=now + timedelta(days=1),
    )
    snapshot_reminder = await _create_due_reminder(
        app.state.reminder_service,
        person_id="person_media_cleanup",
        now=now,
        media_asset_id=asset_ids[1],
    )
    assert await app.state.reminder_service.claim_due(worker_id="cleanup-snapshot", now=now) == [
        snapshot_reminder.id
    ]
    await app.state.reminder_service.trigger_claimed(
        snapshot_reminder.id, worker_id="cleanup-snapshot", now=now
    )
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(Reminder).where(Reminder.id == snapshot_reminder.id).values(media_asset_id=None)
        )

    worker = MediaCleanupWorker(
        app.state.session_factory,
        storage,
        _FixedClock(now),
        batch_size=2,
    )
    assert await worker.run_once() == 2

    async with app.state.session_factory() as session:
        remaining = set(
            await session.scalars(select(MediaAsset.id).where(MediaAsset.id.in_(asset_ids)))
        )
    assert remaining == {asset_ids[0], asset_ids[1], asset_ids[4]}
    assert stored_paths[asset_ids[0]].exists()
    assert stored_paths[asset_ids[1]].exists()
    assert not stored_paths[asset_ids[2]].exists()
    assert not stored_paths[asset_ids[3]].exists()
    assert stored_paths[asset_ids[4]].exists()

    assert await worker.run_once() == 1
    async with app.state.session_factory() as session:
        remaining = set(
            await session.scalars(select(MediaAsset.id).where(MediaAsset.id.in_(asset_ids)))
        )
    assert remaining == {asset_ids[0], asset_ids[1]}
    assert stored_paths[asset_ids[0]].exists()
    assert stored_paths[asset_ids[1]].exists()
    assert not stored_paths[asset_ids[4]].exists()
