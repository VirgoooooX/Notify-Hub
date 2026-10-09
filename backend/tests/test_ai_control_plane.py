from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.application.ai_control_service import HUB_CONNECTION_ID, AIControlService
from app.config import Settings
from app.infrastructure.database import Base
from app.infrastructure.database.ai_models import AIProfile, AIProvider
from app.infrastructure.database.models import AuditLog
from app.infrastructure.database.session import create_session_factory
from app.main import create_app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine


async def _admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "administrator", "password": "correct-horse-battery-staple"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


@pytest.mark.asyncio
async def test_hub_connection_and_business_profiles_require_admin(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = api
    assert (await client.get("/api/v1/admin/ai/hub")).status_code == 401
    headers = await _admin_headers(client)
    initial = await client.get("/api/v1/admin/ai/hub", headers=headers)
    assert initial.json()["data"]["enabled"] is False
    assert initial.json()["data"]["application_key_configured"] is False
    saved = await client.put(
        "/api/v1/admin/ai/hub",
        headers=headers,
        json={
            "base_url": "http://127.0.0.1:8848/api/v1/",
            "allow_private_network": True,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["base_url"] == "http://127.0.0.1:8848"
    assert (
        await client.post("/api/v1/admin/ai/profiles", headers=headers, json={"name": "local"})
    ).status_code == 404
    assert (
        await client.patch(
            "/api/v1/admin/ai/profiles/central", headers=headers, json={"temperature": 1}
        )
    ).status_code == 404
    assert (
        await client.delete("/api/v1/admin/ai/profiles/central", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/admin/ai/profiles", headers=headers)).status_code == 502
    assert (await client.get("/api/v1/admin/ai/providers", headers=headers)).status_code == 404
    assert (
        await client.put(
            "/api/v1/admin/ai/hub/application-key", headers=headers, json={"value": "test-secret"}
        )
    ).status_code == 503
    async with app.state.session_factory() as session:
        assert await session.scalar(select(AuditLog).where(AuditLog.action == "ai.hub.update"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"base_url": "http://127.0.0.1:8848"},
        {"base_url": "https://user:password@hub.example.test"},
        {"base_url": "https://hub.example.test?token=secret"},
        {"base_url": "https://hub.example.test#fragment"},
        {"base_url": "https://hub.example.test", "verify_tls": False},
    ],
)
async def test_hub_rejects_unsafe_connection_configuration(
    api: tuple[httpx.AsyncClient, object], values: dict[str, object]
) -> None:
    client, _app = api
    headers = await _admin_headers(client)
    result = await client.put("/api/v1/admin/ai/hub", headers=headers, json=values)
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_hub_exports_existing_profile_ids_and_legacy_model_choices(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = api
    headers = await _admin_headers(client)
    control = app.state.ai_control_service
    await control.ensure_connection()
    async with app.state.session_factory() as session, session.begin():
        session.add(
            AIProfile(
                id="existing_summary",
                name="Summary",
                capability="summarize",
                provider_id=HUB_CONNECTION_ID,
                model="",
                system_instructions="Private business prompt",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    async with app.state.session_factory() as session, session.begin():
        now = datetime.now(UTC)
        session.add(
            AIProvider(
                id="aip_legacy",
                name="Legacy",
                preset="custom",
                protocol="openai_responses",
                base_url="https://legacy.example.test",
                enabled=False,
                allow_private_network=False,
                timeout_seconds=30,
                max_retries=2,
                verify_tls=True,
                structured_output_mode="auto",
                custom_query={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        profile = await session.get(AIProfile, "existing_summary")
        profile.provider_id = "aip_legacy"
        profile.model = "historical-model"
    exported = await client.get("/api/v1/admin/ai/hub/routing", headers=headers)
    assert exported.status_code == 200
    route = exported.json()["data"]["profiles"]["existing_summary"]
    assert route["protocol"] == "responses" and route["models"] == ["historical-model"]
    assert route["purpose"] == "summarize" and route["parameters"]["max_output_tokens"] == 160
    assert "Private business prompt" not in exported.text
    assert "legacy.example.test" not in exported.text


@pytest.mark.asyncio
async def test_hub_application_key_is_encrypted_and_never_returned(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'secrets.db').as_posix()}",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        secret_encryption_key="test-encryption-key-that-is-long-enough",
    )
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _admin_headers(client)
        secret = "test-application-key-never-echo"
        saved = await client.put(
            "/api/v1/admin/ai/hub/application-key", headers=headers, json={"value": secret}
        )
        assert saved.status_code == 200 and secret not in saved.text
        state = await client.get("/api/v1/admin/ai/hub", headers=headers)
        assert state.json()["data"]["application_key_configured"] is True
        assert secret not in state.text
        oversized = secret * 300
        invalid = await client.put(
            "/api/v1/admin/ai/hub/application-key", headers=headers, json={"value": oversized}
        )
        assert invalid.status_code == 422 and secret not in invalid.text
        assert (
            await app.state.secret_store.get("ai_hub", HUB_CONNECTION_ID, "application_key")
            == secret
        )
        assert secret.encode() not in (tmp_path / "secrets.db").read_bytes()
        assert (
            await client.delete("/api/v1/admin/ai/hub/application-key", headers=headers)
        ).status_code == 204
        assert (
            await app.state.secret_store.get("ai_hub", HUB_CONNECTION_ID, "application_key") is None
        )
    await app.state.engine.dispose()


@pytest.mark.asyncio
async def test_hub_bootstrap_is_idempotent_and_keeps_existing_profiles(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'bootstrap.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    control = AIControlService(create_session_factory(engine))
    await control.bootstrap_connection(
        base_url=None, application_key=None, allow_private_network=False
    )
    assert (await control.get_connection())["enabled"] is False
    await control.bootstrap_connection(
        base_url="http://127.0.0.1:8848",
        application_key=None,
        allow_private_network=True,
    )
    await control.bootstrap_connection(
        base_url="https://different.example.test",
        application_key=None,
        allow_private_network=False,
    )
    assert (await control.get_connection())["base_url"] == "http://127.0.0.1:8848"
    await engine.dispose()
