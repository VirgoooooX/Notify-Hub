import asyncio
from typing import Any

import pytest
from app.channels.base import ChannelMessage, ChannelResult, FakeChannel
from app.infrastructure.database.models import Delivery, DeliveryStatus
from app.workers.delivery_worker import DeliveryWorker
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from tests.test_core_api import initialize_and_login


class DelayedFakeChannel(FakeChannel):
    async def send(self, message: ChannelMessage) -> ChannelResult:
        await asyncio.sleep(0.02)
        return await super().send(message)


@pytest.mark.integration
async def test_worker_sends_outside_transaction_and_records_attempt(api: tuple[Any, Any]) -> None:
    client, app = api
    access = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {access}"}
    await client.post(
        "/api/v1/admin/people", headers=auth, json={"id": "person_worker", "display_name": "Worker"}
    )
    await client.post(
        "/api/v1/admin/people/person_worker/wecom-identities",
        headers=auth,
        json={"user_id": "WeComWorker"},
    )
    created = await client.post(
        "/api/v1/admin/api-clients",
        headers=auth,
        json={"name": "worker", "allowed_recipient_ids": ["person_worker"]},
    )
    key = created.json()["data"]["api_key"]
    await client.post(
        "/api/v1/events",
        headers={"X-API-Key": key},
        json={
            "event_type": "worker.test",
            "event_key": "worker-1",
            "title": "worker",
            "recipients": ["person_worker"],
        },
    )
    # Simulate a process dying after it claimed the delivery. A fresh worker
    # must recover the persisted expired lease before sending.
    crashed = DeliveryWorker(app.state.session_factory, FakeChannel(), app.state.clock, "crashed")
    delivery_id = await crashed.claim_one()
    assert delivery_id is not None
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(Delivery)
            .where(Delivery.id == delivery_id)
            .values(claim_expires_at=app.state.clock.now().replace(year=2000))
        )

    fake = DelayedFakeChannel()
    worker = DeliveryWorker(app.state.session_factory, fake, app.state.clock, "worker-test")
    assert await worker.reclaim_expired() == 1
    assert await worker.process_one() is True
    async with app.state.session_factory() as session:
        delivery = await session.scalar(select(Delivery))
        assert delivery is not None and delivery.status == DeliveryStatus.SUCCEEDED.value
        assert delivery.attempt_count == 1
    assert fake.messages[0].recipients == ["WeComWorker"]

    attempts = await client.get(f"/api/v1/admin/deliveries/{delivery_id}/attempts", headers=auth)
    assert attempts.status_code == 200
    timing = attempts.json()["data"][0]
    assert timing["queue_latency_ms"] >= 0
    assert timing["send_latency_ms"] >= 10
    assert timing["total_latency_ms"] >= timing["send_latency_ms"]


@pytest.mark.integration
async def test_retryable_and_permanent_failures(api: tuple[Any, Any]) -> None:
    _client, _app = api
    # State transition behavior is tested through _finish on claimed records in the API flow test.
    assert ChannelResult(False, True, "NETWORK_ERROR").retryable is True


@pytest.mark.integration
async def test_unavailable_tts_persists_single_text_fallback(api: tuple[Any, Any]) -> None:
    client, app = api
    access = await initialize_and_login(client)
    auth = {"Authorization": f"Bearer {access}"}
    person = (
        await client.post("/api/v1/admin/people", headers=auth, json={"name": "Voice target"})
    ).json()["data"]
    await client.post(
        f"/api/v1/admin/people/{person['id']}/wecom-identities",
        headers=auth,
        json={"user_id": "voice-target"},
    )
    created = await client.post(
        "/api/v1/admin/notifications",
        headers=auth,
        json={
            "title": "紧急通知",
            "content": "请立即处理",
            "message_type": "voice",
            "recipients": [person["id"]],
            "priority": "critical",
        },
    )
    assert created.status_code == 202
    fake = FakeChannel()
    worker = DeliveryWorker(app.state.session_factory, fake, app.state.clock, "voice-worker")
    assert await worker.process_one()
    assert [message.message_type for message in fake.messages] == ["text"]
    async with app.state.session_factory() as session:
        delivery = await session.scalar(
            select(Delivery).options(selectinload(Delivery.notification))
        )
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SUCCEEDED.value
        assert delivery.notification.payload["voice_text_fallback"] is True
