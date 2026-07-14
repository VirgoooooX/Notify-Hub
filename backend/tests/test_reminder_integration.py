import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.application.reminder_service import ReminderCreate, ReminderService
from app.application.runtime_adapters import ReminderEventEmitterAdapter
from app.channels.base import FakeChannel
from app.domain.reminders import AckPolicy, ScheduleType, hash_action_token
from app.infrastructure.database.models import Delivery, Event, Notification
from app.infrastructure.database.reminder_models import NotificationAction
from app.workers.delivery_worker import DeliveryWorker
from app.workers.reminder_worker import ReminderWorker
from sqlalchemy import select

from tests.test_core_api import initialize_and_login


@pytest.mark.integration
async def test_persisted_reminder_is_recovered_after_worker_restart_without_duplicate(
    api: tuple[Any, Any],
) -> None:
    client, app = api
    access = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {access}"}
    person = (
        await client.post("/api/v1/admin/people", headers=auth, json={"name": "Receiver"})
    ).json()["data"]
    now = datetime.now(UTC)
    reminder = (
        await client.post(
            "/api/v1/admin/reminders",
            headers=auth,
            json={
                "title": "restart recovery",
                "schedule": {
                    "type": "once",
                    "at": (now - timedelta(seconds=1)).isoformat(),
                },
                "recipients": [person["id"]],
                "require_ack": False,
            },
        )
    ).json()["data"]

    restarted_service = ReminderService(
        app.state.session_factory,
        ReminderEventEmitterAdapter(app.state.event_service),
        "restart-test-secret",
    )
    restarted_worker = ReminderWorker(restarted_service, worker_id="reminder-restarted")
    assert await restarted_worker.run_once(now=now) == 1

    second_service = ReminderService(
        app.state.session_factory,
        ReminderEventEmitterAdapter(app.state.event_service),
        "restart-test-secret",
    )
    second_worker = ReminderWorker(second_service, worker_id="reminder-restarted-again")
    assert await second_worker.run_once(now=now) == 0

    async with app.state.session_factory() as session:
        events = list(await session.scalars(select(Event).where(Event.source_id == reminder["id"])))
        notifications = list(
            await session.scalars(
                select(Notification).where(Notification.reminder_id == reminder["id"])
            )
        )
        deliveries = list(
            await session.scalars(
                select(Delivery)
                .join(Notification, Notification.id == Delivery.notification_id)
                .where(Notification.reminder_id == reminder["id"])
            )
        )

    assert len(events) == len(notifications) == len(deliveries) == 1
    assert events[0].payload["occurrence"] == 1
    assert notifications[0].event_id == events[0].id
    assert deliveries[0].notification_id == notifications[0].id


@pytest.mark.integration
async def test_reminder_token_is_reconstructed_only_at_delivery(api: tuple[Any, Any]) -> None:
    client, app = api
    access = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {access}"}
    person = (
        await client.post("/api/v1/admin/people", headers=auth, json={"name": "Receiver"})
    ).json()["data"]
    await client.post(
        f"/api/v1/admin/people/{person['id']}/wecom-identities",
        headers=auth,
        json={"user_id": "receiver-user"},
    )
    now = datetime.now(UTC)
    reminder = (
        await client.post(
            "/api/v1/admin/reminders",
            headers=auth,
            json={
                "title": "确认任务",
                "schedule": {
                    "type": "once",
                    "at": (now - timedelta(seconds=1)).isoformat(),
                },
                "recipients": [person["id"]],
                "require_ack": True,
            },
        )
    ).json()["data"]
    claimed = await app.state.reminder_service.claim_due(worker_id="test", now=now)
    assert claimed == [reminder["id"]]
    await app.state.reminder_service.trigger_claimed(reminder["id"], worker_id="test", now=now)

    async with app.state.session_factory() as session:
        event = await session.scalar(select(Event).where(Event.source_id == reminder["id"]))
        notification = await session.scalar(
            select(Notification).where(Notification.reminder_id == reminder["id"])
        )
        action = await session.scalar(
            select(NotificationAction).where(NotificationAction.reminder_id == reminder["id"])
        )
    assert event is not None and notification is not None and action is not None
    assert "action_tokens" not in event.payload
    assert "action_tokens" not in notification.payload
    assert event.payload["action_ids"][person["id"]] == action.id

    fake = FakeChannel()
    worker = DeliveryWorker(
        app.state.session_factory,
        fake,
        app.state.clock,
        "delivery-test",
        action_token_for_id=app.state.reminder_service.action_token,
    )
    assert await worker.process_one()
    token = fake.messages[0].payload["action_token"]
    assert isinstance(token, str)
    assert hash_action_token(token) == action.token_hash
    assert fake.messages[0].payload["task_id"] == action.id


@pytest.mark.integration
async def test_concurrent_cancel_prevents_new_reminder_delivery(api: tuple[Any, Any]) -> None:
    client, app = api
    access = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {access}"}
    person = (
        await client.post("/api/v1/admin/people", headers=auth, json={"name": "Receiver"})
    ).json()["data"]
    entered, release = asyncio.Event(), asyncio.Event()
    delegate = ReminderEventEmitterAdapter(app.state.event_service)

    async def blocking_emit(draft: Any) -> Any:
        entered.set()
        await release.wait()
        return await delegate(draft)

    service = ReminderService(app.state.session_factory, blocking_emit, "test-secret")
    now = datetime.now(UTC)
    reminder = await service.create(
        ReminderCreate(
            creator_person_id=person["id"],
            title="race",
            content="",
            schedule_type=ScheduleType.ONCE,
            timezone="UTC",
            recipient_ids=(person["id"],),
            scheduled_at=now - timedelta(seconds=1),
            require_ack=True,
            ack_policy=AckPolicy.ANY,
        ),
        now=now,
    )
    assert await service.claim_due(worker_id="race-worker", now=now) == [reminder.id]
    trigger = asyncio.create_task(
        service.trigger_claimed(reminder.id, worker_id="race-worker", now=now)
    )
    await entered.wait()
    await service.cancel(reminder.id, now=now)
    release.set()
    await trigger
    async with app.state.session_factory() as session:
        statuses = list(
            await session.scalars(
                select(Delivery.status)
                .join(Notification, Notification.id == Delivery.notification_id)
                .join(Event, Event.id == Notification.event_id)
                .where(Event.source_id == reminder.id)
            )
        )
    assert statuses and set(statuses) == {"cancelled"}
