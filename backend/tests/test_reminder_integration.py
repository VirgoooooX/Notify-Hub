import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.application.reminder_service import ReminderCreate, ReminderService
from app.application.runtime_adapters import ReminderEventEmitterAdapter
from app.channels.base import FakeChannel
from app.domain.reminders import AckPolicy, ScheduleType
from app.infrastructure.database.models import Delivery, Event, Notification, WeComIdentity
from app.workers.delivery_worker import DeliveryWorker
from app.workers.reminder_worker import ReminderWorker
from sqlalchemy import select

from tests.test_core_api import initialize_and_login


@pytest.mark.integration
async def test_interval_and_cron_api_are_connected_to_planner(
    api: tuple[Any, Any],
) -> None:
    client, app = api
    access = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {access}"}
    person = (
        await client.post("/api/v1/admin/people", headers=auth, json={"name": "Schedule User"})
    ).json()["data"]
    start = datetime.now(UTC) + timedelta(minutes=1)
    created = await client.post(
        "/api/v1/admin/reminders",
        headers=auth,
        json={
            "title": "anchored interval",
            "schedule": {
                "type": "interval",
                "interval_seconds": 300,
                "start_at": start.isoformat(),
                "timezone": "UTC",
                "misfire_policy": "fire_once",
            },
            "recipients": [person["id"]],
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["data"]["id"]
    worker = ReminderWorker(app.state.reminder_service, worker_id="interval-test")
    assert await worker.run_once(now=start) == 1
    reminder = await app.state.reminder_service.get(reminder_id)
    next_run = reminder.next_run_at
    assert next_run is not None
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=UTC)
    assert next_run == start + timedelta(minutes=5)

    preview = await client.post(
        "/api/v1/admin/reminders/preview",
        headers=auth,
        json={
            "type": "cron",
            "cron_expression": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "start_at": start.isoformat(),
            "count": 5,
        },
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["data"]["triggers"]) == 5


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
    scheduled_at = now + timedelta(minutes=1)
    worker_now = scheduled_at + timedelta(seconds=1)
    reminder = (
        await client.post(
            "/api/v1/admin/reminders",
            headers=auth,
            json={
                "title": "restart recovery",
                "schedule": {
                    "type": "once",
                    "at": scheduled_at.isoformat(),
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
    assert await restarted_worker.run_once(now=worker_now) == 1

    second_service = ReminderService(
        app.state.session_factory,
        ReminderEventEmitterAdapter(app.state.event_service),
        "restart-test-secret",
    )
    second_worker = ReminderWorker(second_service, worker_id="reminder-restarted-again")
    assert await second_worker.run_once(now=worker_now) == 0

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
async def test_interactive_reminder_uses_plain_message_and_updates_latest_pointer(
    api: tuple[Any, Any],
) -> None:
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
    scheduled_at = now + timedelta(minutes=1)
    worker_now = scheduled_at + timedelta(seconds=1)
    reminder = (
        await client.post(
            "/api/v1/admin/reminders",
            headers=auth,
            json={
                "title": "确认任务",
                "schedule": {
                    "type": "once",
                    "at": scheduled_at.isoformat(),
                },
                "recipients": [person["id"]],
                "require_ack": True,
            },
        )
    ).json()["data"]
    claimed = await app.state.reminder_service.claim_due(worker_id="test", now=worker_now)
    assert claimed == [reminder["id"]]
    await app.state.reminder_service.trigger_claimed(
        reminder["id"], worker_id="test", now=worker_now
    )
    due_recipients = await app.state.reminder_service.claim_due_recipients(
        worker_id="test", now=worker_now
    )
    for recipient_id in due_recipients:
        await app.state.reminder_service.notify_recipient(
            recipient_id, worker_id="test", now=worker_now
        )

    async with app.state.session_factory() as session:
        event = await session.scalar(select(Event).where(Event.source_id == reminder["id"]))
        notification = await session.scalar(
            select(Notification).where(Notification.reminder_id == reminder["id"])
        )
    assert event is not None and notification is not None
    assert notification.message_type == "text"
    assert "【快捷操作】→【完成本次】" in notification.content
    assert notification.payload["interactive_reminder"] is True
    assert "action_token" not in notification.payload

    fake = FakeChannel()
    worker = DeliveryWorker(
        app.state.session_factory,
        fake,
        app.state.clock,
        "delivery-test",
    )
    assert await worker.process_one()
    assert fake.messages[0].message_type == "text"
    assert "【快捷操作】→【完成本次】" in fake.messages[0].content
    assert "action_token" not in fake.messages[0].payload
    async with app.state.session_factory() as session:
        identity = await session.scalar(
            select(WeComIdentity).where(WeComIdentity.user_id == "receiver-user")
        )
    assert identity is not None
    assert identity.latest_interactive_occurrence_id == notification.reminder_occurrence_id


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
            scheduled_at=now,
            require_ack=True,
            ack_policy=AckPolicy.ANY,
        ),
        now=now,
    )
    assert await service.claim_due(worker_id="race-worker", now=now) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="race-worker", now=now)
    due_recipients = await service.claim_due_recipients(worker_id="race-worker", now=now)
    assert len(due_recipients) == 1
    trigger = asyncio.create_task(
        service.notify_recipient(due_recipients[0], worker_id="race-worker", now=now)
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
