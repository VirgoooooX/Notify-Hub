from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from app.application.ai_control_service import AIControlService
from app.application.plugin_service import PluginAIProfileUnavailableError
from app.config import Settings
from app.infrastructure.database import Base
from app.infrastructure.database.ai_models import AIProfile
from app.infrastructure.database.models import AuditLog
from app.infrastructure.database.plugin_models import PluginConfig, PluginRecord
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
async def test_ai_control_plane_requires_admin_and_round_trips(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = api
    assert (await client.get("/api/v1/admin/ai/providers")).status_code == 401
    headers = await _admin_headers(client)

    created = await client.post(
        "/api/v1/admin/ai/providers",
        headers=headers,
        json={
            "id": "aip_test",
            "name": "Test compatible endpoint",
            "preset": "custom",
            "base_url": "https://provider.example.test/v1/",
        },
    )
    assert created.status_code == 201
    provider = created.json()["data"]
    assert provider["base_url"] == "https://provider.example.test/v1"
    assert provider["api_key_configured"] is False
    assert "api_key" not in provider

    await app.state.ai_control_service.sync_provider_models(
        provider["id"],
        ["deployment-name", "replacement-deployment", "disabled-deployment"],
    )
    await app.state.ai_control_service.set_allowed_models(
        provider["id"], ["deployment-name", "replacement-deployment"]
    )

    profile_response = await client.post(
        "/api/v1/admin/ai/profiles",
        headers=headers,
        json={
            "id": "semantic_classifier_fast",
            "name": "Fast semantic classifier",
            "provider_id": provider["id"],
            "model": "deployment-name",
        },
    )
    assert profile_response.status_code == 201
    profile = profile_response.json()["data"]
    assert profile["revision"] == 1

    changed = await client.patch(
        f"/api/v1/admin/ai/profiles/{profile['id']}",
        headers=headers,
        json={"model": "replacement-deployment", "daily_request_limit": 5},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["revision"] == 2
    cleared = await client.patch(
        f"/api/v1/admin/ai/profiles/{profile['id']}",
        headers=headers,
        json={"daily_request_limit": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["daily_request_limit"] is None

    listed = await client.get("/api/v1/admin/ai/providers", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["api_key_configured"] is False

    secret_attempt = await client.put(
        f"/api/v1/admin/ai/providers/{provider['id']}/api-key",
        headers=headers,
        json={"value": "test-provider-secret-that-must-not-echo"},
    )
    assert secret_attempt.status_code == 503
    assert "test-provider-secret" not in secret_attempt.text

    async with app.state.session_factory() as session:
        actions = list(
            await session.scalars(
                select(AuditLog.action)
                .where(AuditLog.resource_type.in_(["ai_provider", "ai_profile"]))
                .order_by(AuditLog.created_at)
            )
        )
    assert actions == [
        "ai.provider.create",
        "ai.profile.create",
        "ai.profile.update",
        "ai.profile.update",
    ]


@pytest.mark.asyncio
async def test_ai_provider_url_and_profile_constraints(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, _app = api
    headers = await _admin_headers(client)

    for unsafe_url in (
        "http://provider.example.test/v1",
        "https://user:password@provider.example.test/v1",
        "https://provider.example.test/v1#fragment",
    ):
        response = await client.post(
            "/api/v1/admin/ai/providers",
            headers=headers,
            json={"name": "unsafe", "preset": "custom", "base_url": unsafe_url},
        )
        assert response.status_code == 422

    missing_provider = await client.post(
        "/api/v1/admin/ai/profiles",
        headers=headers,
        json={
            "name": "Broken",
            "provider_id": "aip_missing",
            "model": "missing-model",
        },
    )
    assert missing_provider.status_code == 404


@pytest.mark.asyncio
async def test_ai_profile_rejects_models_not_explicitly_enabled(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = api
    headers = await _admin_headers(client)
    provider = await app.state.ai_control_service.create_provider(
        {
            "id": "aip_allowlist",
            "name": "Allowlist",
            "preset": "custom",
            "protocol": "openai_chat_completions",
            "base_url": "https://provider.example.test/v1",
            "enabled": True,
            "allow_private_network": False,
            "timeout_seconds": 5,
            "max_retries": 0,
            "verify_tls": True,
            "structured_output_mode": "auto",
            "custom_query": {},
        }
    )
    await app.state.ai_control_service.sync_provider_models(
        provider["id"], ["allowed-model", "blocked-model"]
    )
    await app.state.ai_control_service.set_allowed_models(provider["id"], ["allowed-model"])
    rejected = await client.post(
        "/api/v1/admin/ai/profiles",
        headers=headers,
        json={
            "name": "Rejected",
            "provider_id": provider["id"],
            "model": "blocked-model",
        },
    )
    assert rejected.status_code == 422
    assert "explicitly enabled" in rejected.text


@pytest.mark.asyncio
async def test_ai_provider_models_sync_and_allowlist_api(
    api: tuple[httpx.AsyncClient, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app = api
    headers = await _admin_headers(client)
    created = await client.post(
        "/api/v1/admin/ai/providers",
        headers=headers,
        json={
            "id": "aip_model_sync",
            "name": "Model sync",
            "preset": "custom",
            "base_url": "https://provider.example.test/v1",
        },
    )
    provider_id = created.json()["data"]["id"]
    monkeypatch.setattr(
        app.state.ai_service,
        "list_models",
        AsyncMock(return_value=["model-b", "model-a"]),
    )
    synced = await client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/models/sync", headers=headers
    )
    assert synced.status_code == 200
    assert [item["model_id"] for item in synced.json()["data"]["models"]] == [
        "model-a",
        "model-b",
    ]
    assert not any(item["enabled"] for item in synced.json()["data"]["models"])

    allowed = await client.put(
        f"/api/v1/admin/ai/providers/{provider_id}/models/allowed",
        headers=headers,
        json={"model_ids": ["model-b"]},
    )
    assert allowed.status_code == 200
    by_id = {item["model_id"]: item for item in allowed.json()["data"]["models"]}
    assert by_id["model-b"]["enabled"] is True
    assert by_id["model-a"]["enabled"] is False

    app.state.ai_service.list_models.return_value = ["model-a"]
    refreshed = await client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/models/sync", headers=headers
    )
    refreshed_by_id = {item["model_id"]: item for item in refreshed.json()["data"]["models"]}
    assert refreshed_by_id["model-b"]["available"] is False
    assert refreshed_by_id["model-b"]["enabled"] is False


@pytest.mark.asyncio
async def test_ai_profile_delete_is_soft_and_blocks_active_plugin(
    api: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = api
    headers = await _admin_headers(client)
    control = app.state.ai_control_service
    provider = await control.create_provider(
        {
            "id": "aip_delete_profile",
            "name": "Delete profile",
            "preset": "custom",
            "protocol": "openai_chat_completions",
            "base_url": "https://provider.example.test/v1",
            "enabled": True,
            "allow_private_network": False,
            "timeout_seconds": 5,
            "max_retries": 0,
            "verify_tls": True,
            "structured_output_mode": "auto",
            "custom_query": {},
        }
    )
    await control.sync_provider_models(provider["id"], ["model-delete"])
    await control.set_allowed_models(provider["id"], ["model-delete"])
    profile = await control.create_profile(
        {
            "id": "profile_delete_test",
            "name": "Delete test",
            "description": "Deletion policy",
            "capability": "classify",
            "provider_id": provider["id"],
            "model": "model-delete",
            "temperature": 0,
            "max_output_tokens": 100,
            "response_format": "auto",
            "timeout_seconds": 5,
            "cache_ttl_seconds": 60,
            "daily_request_limit": None,
            "daily_token_limit": None,
            "enabled": True,
        }
    )
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            PluginRecord(
                id="plugin_profile_user",
                name="Profile user",
                version="1.0.0",
                description="",
                install_type="builtin",
                enabled=True,
                status="active",
                consecutive_failures=0,
                circuit_open=False,
                manifest={
                    "id": "plugin_profile_user",
                    "name": "Profile user",
                    "version": "1.0.0",
                    "description": "",
                    "entrypoint": "test.module:Plugin",
                    "api_version": "1",
                    "kind": "monitor",
                    "trusted": True,
                    "default_schedule": {"type": "interval", "seconds": 60},
                    "max_concurrency": 1,
                    "timeout_seconds": 60,
                    "permissions": {"ai_profiles": [profile["id"]]},
                },
                schedule={},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PluginConfig(
                plugin_id="plugin_profile_user",
                config={"analysis": {"profile": profile["id"]}},
                schema_version=1,
                updated_at=now,
            )
        )
    blocked = await client.delete(f"/api/v1/admin/ai/profiles/{profile['id']}", headers=headers)
    assert blocked.status_code == 409

    async with app.state.session_factory() as session, session.begin():
        plugin = await session.get(PluginRecord, "plugin_profile_user")
        assert plugin is not None
        plugin.enabled = False
    deleted = await client.delete(f"/api/v1/admin/ai/profiles/{profile['id']}", headers=headers)
    assert deleted.status_code == 204
    assert profile["id"] not in {item["id"] for item in await control.list_profiles()}
    async with app.state.session_factory() as session:
        deleted_row = await session.get(AIProfile, profile["id"])
        assert deleted_row is not None
        assert deleted_row.deleted_at is not None
        assert deleted_row.enabled is False
        assert deleted_row.revision == 2
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "ai.profile.delete",
                AuditLog.resource_id == profile["id"],
            )
        )
        assert audit is not None

    with pytest.raises(PluginAIProfileUnavailableError):
        await app.state.plugin_service.enable("plugin_profile_user")


@pytest.mark.asyncio
async def test_ai_provider_api_key_is_encrypted_and_never_returned(tmp_path: Path) -> None:
    root = tmp_path
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(root / 'secrets.db').as_posix()}",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        secret_encryption_key="test-encryption-key-that-is-long-enough",
    )
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _admin_headers(client)
        secret = "test-provider-key-that-must-never-echo"
        created = await client.post(
            "/api/v1/admin/ai/providers",
            headers=headers,
            json={
                "id": "aip_secret_test",
                "name": "Secret test",
                "preset": "custom",
                "base_url": "https://provider.example.test/v1",
                "api_key": secret,
            },
        )
        provider_id = created.json()["data"]["id"]
        assert created.status_code == 201
        assert created.json()["data"]["api_key_configured"] is True
        assert secret not in created.text
        listed = await client.get("/api/v1/admin/ai/providers", headers=headers)
        assert listed.json()["data"][0]["api_key_configured"] is True
        assert secret not in listed.text
        assert await app.state.secret_store.get("ai_provider", provider_id, "api_key") == secret
    await app.state.engine.dispose()


@pytest.mark.asyncio
async def test_ai_bootstrap_can_start_without_api_key(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'bootstrap.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    control = AIControlService(create_session_factory(engine))
    incomplete = await control.bootstrap_if_empty(
        enabled=True,
        preset="custom",
        base_url=None,
        model=None,
        api_key=None,
    )
    created = await control.bootstrap_if_empty(
        enabled=True,
        preset="deepseek",
        base_url=None,
        model="deepseek-chat",
        api_key=None,
    )
    repeated = await control.bootstrap_if_empty(
        enabled=True,
        preset="custom",
        base_url="https://must-not-overwrite.example/v1",
        model="replacement",
        api_key=None,
    )
    providers = await control.list_providers()
    profiles = await control.list_profiles()
    assert incomplete is False
    assert created is True
    assert repeated is False
    assert providers[0]["base_url"] == "https://api.deepseek.com"
    assert profiles[0]["model"] == "deepseek-chat"
    await engine.dispose()
