from typing import Any

import httpx
import pytest
from app.infrastructure.database.models import Delivery, Event, Notification
from sqlalchemy import func, select


async def initialize_and_login(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "admin", "password": "strong-test-password"},
    )
    assert response.status_code == 201, response.text
    second = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "admin2", "password": "strong-test-password"},
    )
    assert second.status_code == 409
    response = await client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "strong-test-password"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["access_token"])


@pytest.mark.integration
async def test_event_is_persisted_and_duplicate_is_idempotent(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = api
    access = await initialize_and_login(client)
    headers = {"Authorization": f"Bearer {access}"}
    person = await client.post(
        "/api/v1/admin/people",
        headers=headers,
        json={"id": "person_test", "display_name": "Test"},
    )
    assert person.status_code == 201, person.text
    created = await client.post(
        "/api/v1/admin/api-clients",
        headers=headers,
        json={
            "id": "client_test",
            "name": "test client",
            "allowed_event_types": ["system.test"],
            "allowed_recipient_ids": ["person_test"],
        },
    )
    assert created.status_code == 201, created.text
    api_key = created.json()["data"]["api_key"]
    body = {
        "event_type": "system.test",
        "event_key": "stable-1",
        "title": "Test",
        "content": "persist me",
        "recipients": ["person_test"],
    }
    first = await client.post("/api/v1/events", headers={"X-API-Key": api_key}, json=body)
    duplicate = await client.post("/api/v1/events", headers={"X-API-Key": api_key}, json=body)
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["data"]["duplicate"] is False
    assert duplicate.json()["data"] == {
        "event_id": first.json()["data"]["event_id"],
        "status": "accepted",
        "duplicate": True,
    }
    async with app.state.session_factory() as session:
        assert await session.scalar(select(func.count(Event.id))) == 1
        assert await session.scalar(select(func.count(Notification.id))) == 1
        assert await session.scalar(select(func.count(Delivery.id))) == 1


@pytest.mark.integration
async def test_auth_boundaries_and_request_id(api: tuple[httpx.AsyncClient, Any]) -> None:
    client, _app = api
    response = await client.get("/api/v1/admin/events", headers={"X-API-Key": "nfy_invalid"})
    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200


@pytest.mark.integration
async def test_recipient_and_broadcast_permissions(api: tuple[httpx.AsyncClient, Any]) -> None:
    client, _app = api
    access = await initialize_and_login(client)
    created = await client.post(
        "/api/v1/admin/api-clients",
        headers={"Authorization": f"Bearer {access}"},
        json={"name": "restricted", "allowed_recipient_ids": ["person_ok"]},
    )
    api_key = created.json()["data"]["api_key"]
    base = {"event_type": "test", "event_key": "a", "title": "x"}
    forbidden = await client.post(
        "/api/v1/events",
        headers={"X-API-Key": api_key},
        json={**base, "recipients": ["person_other"]},
    )
    assert forbidden.status_code == 403
    broadcast = await client.post(
        "/api/v1/events",
        headers={"X-API-Key": api_key},
        json={**base, "event_key": "b", "recipients": ["@all"], "broadcast": True},
    )
    assert broadcast.status_code == 403
