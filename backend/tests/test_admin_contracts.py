import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest


@pytest.mark.asyncio
async def test_initialize_bootstraps_authenticated_admin_contract(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, _app = api
    status_response = await client.get("/api/v1/admin/auth/status")
    assert status_response.status_code == 200
    assert status_response.json()["data"] == {"initialized": False}

    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "administrator", "password": "correct-horse-battery-staple"},
    )
    assert initialized.status_code == 201
    tokens = initialized.json()["data"]
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["administrator"]["name"] == "administrator"

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await client.get("/api/v1/admin/auth/me", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/admin/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/admin/auth/status")).json()["data"] == {"initialized": True}


@pytest.mark.asyncio
async def test_admin_frontend_contracts_require_auth_and_round_trip(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = api
    unauthenticated = await client.get("/api/v1/admin/plugins")
    assert unauthenticated.status_code == 401

    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "administrator", "password": "correct-horse-battery-staple"},
    )
    token = initialized.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    dashboard = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["today_events"] == 0

    updated = await client.patch(
        "/api/v1/admin/settings",
        headers=headers,
        json={"timezone": "UTC", "retention_days": 90},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["timezone"] == "UTC"
    assert updated.json()["data"]["retention_days"] == 90

    settings_data = updated.json()["data"]["wecom"]
    assert settings_data["api_base_url"] == "https://qyapi.weixin.qq.com"
    assert settings_data["using_proxy"] is False
    app.state.settings.wecom_api_base_url = "https://proxy.example.com/wecom"
    proxied = await client.get("/api/v1/admin/settings", headers=headers)
    assert proxied.json()["data"]["wecom"]["api_base_url"] == ("https://proxy.example.com/wecom")
    assert proxied.json()["data"]["wecom"]["using_proxy"] is True

    person_response = await client.post(
        "/api/v1/admin/people",
        headers=headers,
        json={"name": "值班同事", "is_default": True},
    )
    assert person_response.status_code == 201
    person = person_response.json()["data"]
    assert person["name"] == "值班同事"

    reminder = await client.post(
        "/api/v1/admin/reminders",
        headers=headers,
        json={
            "title": "检查服务",
            "content": "请确认服务健康",
            "schedule": {
                "type": "once",
                "at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "timezone": "UTC",
            },
            "recipients": [person["id"]],
        },
    )
    assert reminder.status_code == 201
    reminder_data = reminder.json()["data"]
    assert reminder_data["creator_person_id"] == person["id"]
    changed = await client.patch(
        f"/api/v1/admin/reminders/{reminder_data['id']}",
        headers=headers,
        json={"title": "检查并记录服务状态"},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["title"] == "检查并记录服务状态"

    page = await client.get("/api/v1/admin/reminders?page=1&page_size=20", headers=headers)
    assert page.status_code == 200
    assert page.json()["data"]["total"] == 1
    assert page.json()["data"]["page_size"] == 20


@pytest.mark.asyncio
async def test_admin_initialization_and_refresh_are_single_use_under_concurrency(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, _app = api
    payloads = [
        {"username": f"administrator{index}", "password": "correct-horse-battery-staple"}
        for index in range(2)
    ]
    initialized = await asyncio.gather(
        *(client.post("/api/v1/admin/auth/initialize", json=payload) for payload in payloads)
    )
    assert sorted(response.status_code for response in initialized) == [201, 409]
    successful = next(response for response in initialized if response.status_code == 201)
    refresh_token = successful.json()["data"]["refresh_token"]
    refreshed = await asyncio.gather(
        *(
            client.post("/api/v1/admin/auth/refresh", json={"refresh_token": refresh_token})
            for _ in range(2)
        )
    )
    assert sorted(response.status_code for response in refreshed) == [200, 401]
