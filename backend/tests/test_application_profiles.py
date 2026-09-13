from __future__ import annotations

import base64
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.application.conversation_service import ConversationState
from app.application.wecom_callback_service import WeComCallbackService
from app.application.wecom_media_service import DatabaseMediaCacheRepository
from app.channels.base import ChannelMessage
from app.channels.wecom.callback import IncomingCallback
from app.config import Settings
from app.domain.clock import SystemClock
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.models import (
    Delivery,
    DeliveryStatus,
    Event,
    Notification,
    Person,
    WeComIdentity,
)
from app.infrastructure.database.profile_models import (
    ApplicationProfile,
    MediaProviderRef,
    ProfileMember,
    WeComProfileConfig,
)
from app.infrastructure.database.reminder_models import ConversationSession, IncomingMessage
from app.profiles.constants import DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY
from app.profiles.errors import ProfileDisabled, ProfileSecretMissing
from app.profiles.registry import ProfileRegistry
from app.profiles.types import DEFAULT_CAPABILITIES
from app.workers.delivery_worker import DeliveryWorker
from sqlalchemy import func, inspect, select, text

from tests.test_core_api import initialize_and_login


def _profile(
    profile_id: str,
    key: str,
    *,
    enabled: bool = True,
    is_default: bool = False,
    capabilities: dict[str, bool] | None = None,
) -> ApplicationProfile:
    now = datetime.now(UTC)
    return ApplicationProfile(
        id=profile_id,
        key=key,
        name=key.title(),
        enabled=enabled,
        is_default=is_default,
        capabilities=dict(capabilities or DEFAULT_CAPABILITIES),
        created_at=now,
        updated_at=now,
    )


async def _add_profile(
    app: Any,
    profile_id: str,
    key: str,
    *,
    agent_id: int | None = None,
    enabled: bool = True,
    capabilities: dict[str, bool] | None = None,
) -> None:
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            _profile(
                profile_id,
                key,
                enabled=enabled,
                capabilities=capabilities,
            )
        )
        if agent_id is not None:
            session.add(
                WeComProfileConfig(
                    profile_id=profile_id,
                    agent_id=agent_id,
                    enabled=enabled,
                    callback_enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )


@pytest.mark.integration
async def test_profile_api_membership_and_records_are_isolated(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = api
    await app.state.profile_registry.ensure_default_profile()
    access = await initialize_and_login(client)
    headers = {"Authorization": f"Bearer {access}"}

    first_person = await client.post(
        "/api/v1/admin/people",
        headers=headers,
        json={"id": "person_profile_a", "display_name": "Profile A"},
    )
    second_person = await client.post(
        "/api/v1/admin/people",
        headers=headers,
        json={"id": "person_profile_b", "display_name": "Profile B"},
    )
    assert first_person.status_code == second_person.status_code == 201

    first = await client.post(
        "/api/v1/admin/profiles",
        headers=headers,
        json={"key": "family-health", "name": "Family Health", "agent_id": 1001},
    )
    second = await client.post(
        "/api/v1/admin/profiles",
        headers=headers,
        json={"key": "home-ops", "name": "Home Ops", "agent_id": 1002},
    )
    assert first.status_code == second.status_code == 201
    first_id = first.json()["data"]["id"]
    second_id = second.json()["data"]["id"]
    assert first.json()["data"]["wecom"]["secret_configured"] is False
    assert "secret-a" not in first.text

    invalid = await client.post(
        "/api/v1/admin/profiles",
        headers=headers,
        json={"key": "Family Health", "name": "Invalid"},
    )
    duplicate = await client.post(
        "/api/v1/admin/profiles",
        headers=headers,
        json={"key": "family-health", "name": "Duplicate"},
    )
    assert invalid.status_code == duplicate.status_code == 409

    assert (
        await client.put(
            f"/api/v1/admin/profiles/{first_id}/members/person_profile_a",
            headers=headers,
            json={"enabled": True},
        )
    ).status_code == 200
    assert (
        await client.put(
            f"/api/v1/admin/profiles/{second_id}/members/person_profile_b",
            headers=headers,
            json={"enabled": True},
        )
    ).status_code == 200

    reminder_payload = {
        "creator_person_id": "person_profile_a",
        "title": "Profile A reminder",
        "content": "A",
        "profile_id": first_id,
        "recipients": ["person_profile_a"],
        "schedule": {"type": "once", "at": "2030-01-01T00:00:00Z"},
    }
    first_reminder = await client.post(
        "/api/v1/admin/reminders", headers=headers, json=reminder_payload
    )
    second_reminder = await client.post(
        "/api/v1/admin/reminders",
        headers=headers,
        json={
            **reminder_payload,
            "creator_person_id": "person_profile_b",
            "title": "Profile B reminder",
            "content": "B",
            "profile_id": second_id,
            "recipients": ["person_profile_b"],
        },
    )
    assert first_reminder.status_code == second_reminder.status_code == 201
    assert first_reminder.json()["data"]["profile_id"] == first_id
    assert second_reminder.json()["data"]["profile_id"] == second_id

    listed_a = await client.get(
        "/api/v1/admin/reminders", headers=headers, params={"profile_id": first_id}
    )
    listed_b = await client.get(
        "/api/v1/admin/reminders", headers=headers, params={"profile_id": second_id}
    )
    assert [item["profile_id"] for item in listed_a.json()["data"]["items"]] == [first_id]
    assert [item["profile_id"] for item in listed_b.json()["data"]["items"]] == [second_id]

    disabled = await client.patch(
        f"/api/v1/admin/profiles/{first_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert (await client.get(f"/api/v1/admin/profiles/{first_id}", headers=headers)).json()["data"][
        "enabled"
    ] is False
    blocked = await client.post(
        f"/api/v1/admin/profiles/{first_id}/wecom/test",
        headers=headers,
        json={"recipient_id": "person_profile_a"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "wecom_profile_disabled"

    default_blocked = await client.patch(
        f"/api/v1/admin/profiles/{DEFAULT_PROFILE_ID}",
        headers=headers,
        json={"enabled": False},
    )
    assert default_blocked.status_code == 409


class MemoryProfileSecrets:
    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self.values = values or {}

    async def get(self, scope_type: str, scope_id: str, name: str) -> str | None:
        assert scope_type == "application_profile"
        return self.values.get((scope_id, name))


@pytest.mark.integration
async def test_profile_registry_has_independent_agent_token_caches(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    await _add_profile(app, "profile_a", "profile-a", agent_id=1001)
    await _add_profile(app, "profile_b", "profile-b", agent_id=1002)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=app.state.settings.database_url,
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        wecom_corp_id="corp-test",
        wecom_agent_id=1,
        wecom_secret="legacy-secret",
        wecom_api_base_url="https://wecom.test",
        allow_broadcast=True,
    )
    secrets = MemoryProfileSecrets(
        {
            ("profile_a", "wecom_secret"): "secret-a",
            ("profile_b", "wecom_secret"): "secret-b",
        }
    )
    token_calls: list[str] = []

    def record_and_respond(request: httpx.Request) -> httpx.Response:
        token_calls.append(request.url.params["corpsecret"])
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "access_token": f"token-{request.url.params['corpsecret']}",
                "expires_in": 7200,
            },
        )

    transport = httpx.MockTransport(record_and_respond)
    clients: list[httpx.AsyncClient] = []

    def http_client_factory(_credentials: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=transport, base_url="https://wecom.test")
        clients.append(client)
        return client

    registry = ProfileRegistry(
        app.state.session_factory,
        settings,
        secret_store=secrets,
        clock=SystemClock(),
        http_client_factory=http_client_factory,
    )
    try:
        runtime_a = await registry.runtime("profile_a")
        runtime_b = await registry.runtime("profile_b")
        assert runtime_a is await registry.runtime("profile_a")
        assert await runtime_a.client.get_access_token() == "token-secret-a"
        assert await runtime_b.client.get_access_token() == "token-secret-b"
        assert token_calls == ["secret-a", "secret-b"]
        assert runtime_a.channel is not runtime_b.channel
        assert runtime_a.channel._agent_id == 1001
        assert runtime_b.channel._agent_id == 1002
    finally:
        await registry.close()
        for client in clients:
            await client.aclose()


@pytest.mark.integration
async def test_profile_registry_requires_secret_and_rejects_disabled_runtime(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    await _add_profile(app, "profile_missing", "profile-missing", agent_id=1003)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=app.state.settings.database_url,
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        wecom_corp_id="corp-test",
        wecom_agent_id=1,
        wecom_secret="legacy-secret",
        wecom_api_base_url="https://wecom.test",
    )
    registry = ProfileRegistry(
        app.state.session_factory,
        settings,
        secret_store=MemoryProfileSecrets(),
        clock=SystemClock(),
    )
    with pytest.raises(ProfileSecretMissing):
        await registry.runtime("profile_missing")
    async with app.state.session_factory() as session, session.begin():
        row = await session.get(ApplicationProfile, "profile_missing")
        assert row is not None
        row.enabled = False
    with pytest.raises(ProfileDisabled):
        await registry.runtime("profile_missing")


@pytest.mark.integration
async def test_callback_only_profile_does_not_require_agent_or_outbound_secret(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    await _add_profile(
        app,
        "profile_callback",
        "callback-only",
        capabilities={**DEFAULT_CAPABILITIES, "outbound_enabled": False},
    )
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            WeComProfileConfig(
                profile_id="profile_callback",
                agent_id=None,
                enabled=True,
                callback_enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=app.state.settings.database_url,
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        wecom_corp_id="corp-test",
        wecom_agent_id=1,
        wecom_secret="legacy-secret",
        wecom_api_base_url="https://wecom.test",
    )
    encoded_key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    registry = ProfileRegistry(
        app.state.session_factory,
        settings,
        secret_store=MemoryProfileSecrets(
            {
                ("profile_callback", "wecom_callback_token"): "callback-token",
                ("profile_callback", "wecom_callback_aes_key"): encoded_key,
            }
        ),
        clock=SystemClock(),
    )
    try:
        crypto = await registry.callback_crypto("callback-only")
        assert crypto is not None
    finally:
        await registry.close()


@pytest.mark.integration
async def test_external_event_client_uses_bound_profile_and_rejects_profile_override(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = api
    await app.state.profile_registry.ensure_default_profile()
    access = await initialize_and_login(client)
    admin_headers = {"Authorization": f"Bearer {access}"}
    person = await client.post(
        "/api/v1/admin/people",
        headers=admin_headers,
        json={"id": "person_family_health", "display_name": "Family Health"},
    )
    assert person.status_code == 201, person.text
    profile = await client.post(
        "/api/v1/admin/profiles",
        headers=admin_headers,
        json={"key": "family-health", "name": "Family Health"},
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["data"]["id"]
    membership = await client.put(
        f"/api/v1/admin/profiles/{profile_id}/members/person_family_health",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert membership.status_code == 200, membership.text
    created = await client.post(
        "/api/v1/admin/api-clients",
        headers=admin_headers,
        json={
            "id": "client_family_health",
            "name": "Family Health",
            "profile_id": profile_id,
            "allowed_event_types": ["family.health.alert"],
            "allowed_recipient_ids": ["person_family_health"],
        },
    )
    assert created.status_code == 201, created.text
    api_key = created.json()["data"]["api_key"]
    event = {
        "event_type": "family.health.alert",
        "event_key": "alert-1",
        "title": "Health alert",
        "content": "Persist this in the bound profile",
        "recipients": ["person_family_health"],
    }
    override = await client.post(
        "/api/v1/events",
        headers={"X-API-Key": api_key},
        json={**event, "profile_id": DEFAULT_PROFILE_ID},
    )
    assert override.status_code == 422
    accepted = await client.post("/api/v1/events", headers={"X-API-Key": api_key}, json=event)
    assert accepted.status_code == 202, accepted.text
    async with app.state.session_factory() as session:
        stored = await session.scalar(select(Event).where(Event.event_key == "alert-1"))
        assert stored is not None
        assert stored.profile_id == profile_id


@pytest.mark.integration
async def test_callback_dedupe_is_scoped_to_profile(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    service = WeComCallbackService(app.state.session_factory)
    now = datetime.now(UTC)
    callback = IncomingCallback(
        sender_external_id="wecom-user",
        provider_message_id="provider-1",
        message_type="text",
        text="hello",
        media_refs={},
        event=None,
        event_key=None,
        action_token=None,
        response_code=None,
        received_at=now,
        dedupe_key="same-dedupe",
        profile_id="profile_a",
    )
    first = await service.accept(callback)
    duplicate = await service.accept(callback)
    other_profile = await service.accept(replace(callback, profile_id="profile_b"))
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert other_profile.duplicate is False
    async with app.state.session_factory() as session:
        assert await session.scalar(select(func.count(IncomingMessage.id))) == 2


@pytest.mark.integration
async def test_delivery_worker_preserves_delivery_profile(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    notification_id = "notification-profile-a"
    delivery_id = "delivery-profile-a"
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Notification(
                id=notification_id,
                profile_id="profile_a",
                event_id=None,
                reminder_id=None,
                reminder_occurrence_id=None,
                message_type="text",
                title="A",
                content="content",
                url=None,
                image_url=None,
                media_asset_id=None,
                ack_policy=None,
                payload={},
                priority="normal",
                require_ack=False,
                created_at=now,
                expires_at=None,
            )
        )
        session.add(
            Delivery(
                id=delivery_id,
                profile_id="profile_a",
                notification_id=notification_id,
                channel="wecom",
                recipient_type="broadcast",
                recipient_id=None,
                status=DeliveryStatus.PROCESSING.value,
                attempt_count=0,
                max_attempts=5,
                next_attempt_at=now,
                claimed_by="worker",
                claim_expires_at=now + timedelta(minutes=2),
                last_error_code=None,
                last_error_message=None,
                provider_message_id=None,
                sent_at=None,
                created_at=now,
                updated_at=now,
            )
        )
    worker = DeliveryWorker(app.state.session_factory, {}, app.state.clock, "worker")
    loaded = await worker._load_message(delivery_id)
    assert loaded is not None
    _channel_name, message = loaded
    assert isinstance(message, ChannelMessage)
    assert message.profile_id == "profile_a"
    assert message.broadcast is True


@pytest.mark.integration
async def test_conversation_sessions_and_mobile_tokens_are_profile_scoped(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Person(
                id="person_conversation",
                display_name="Conversation User",
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WeComIdentity(
                id="identity_conversation",
                person_id="person_conversation",
                user_id="conversation-user",
                active=True,
                latest_interactive_occurrence_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add_all([_profile("profile_a", "profile-a"), _profile("profile_b", "profile-b")])
        session.add_all(
            [
                ProfileMember(
                    id="member-conversation-a",
                    profile_id="profile_a",
                    person_id="person_conversation",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                ),
                ProfileMember(
                    id="member-conversation-b",
                    profile_id="profile_b",
                    person_id="person_conversation",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
    await app.state.conversation_service._upsert_session(
        "identity_conversation", "profile_a", ConversationState.AWAITING_TIME, {}, now
    )
    await app.state.conversation_service._upsert_session(
        "identity_conversation", "profile_b", ConversationState.AWAITING_TIME, {}, now
    )
    token = app.state.mobile_identity_service.issue("identity_conversation", "profile_a")
    with pytest.raises(ValueError, match="another profile"):
        await app.state.mobile_identity_service.resolve(token, "profile_b")
    async with app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(WeComIdentity.id).where(WeComIdentity.id == "identity_conversation")
            )
        ).all()
        assert len(rows) == 1
        session_rows = await session.scalars(select(ConversationSession.profile_id))
        assert set(session_rows) == {"profile_a", "profile_b"}


@pytest.mark.integration
async def test_media_provider_references_are_profile_scoped(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    _client, app = api
    now = datetime.now(UTC)
    async with app.state.session_factory() as session, session.begin():
        session.add_all([_profile("profile_a", "profile-a"), _profile("profile_b", "profile-b")])
        session.add(
            MediaAsset(
                id="media_profile_asset",
                kind="image",
                mime_type="image/png",
                storage_path="profile-asset.png",
                checksum_sha256="0" * 64,
                size_bytes=1,
                duration_seconds=None,
                source="upload",
                created_by=None,
                created_at=now,
                expires_at=None,
                provider_media_id=None,
                provider_expires_at=None,
            )
        )
    repository_a = DatabaseMediaCacheRepository(app.state.session_factory, profile_id="profile_a")
    repository_b = DatabaseMediaCacheRepository(app.state.session_factory, profile_id="profile_b")
    expiry = now + timedelta(hours=1)
    await repository_a.save_provider_cache("media_profile_asset", "provider-a", expiry)
    await repository_b.save_provider_cache("media_profile_asset", "provider-b", expiry)
    cached_a = await repository_a.get_provider_cache("media_profile_asset")
    cached_b = await repository_b.get_provider_cache("media_profile_asset")
    assert cached_a is not None and cached_a.media_id == "provider-a"
    assert cached_b is not None and cached_b.media_id == "provider-b"
    async with app.state.session_factory() as session:
        references = (
            await session.scalars(
                select(MediaProviderRef).where(MediaProviderRef.asset_id == "media_profile_asset")
            )
        ).all()
        assert {(item.profile_id, item.provider_media_id) for item in references} == {
            ("profile_a", "provider-a"),
            ("profile_b", "provider-b"),
        }


@pytest.mark.integration
def test_migration_from_0017_backfills_default_profile_and_scopes_uniques(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    environment = os.environ.copy()
    environment.update(
        {
            "NOTIFY_HUB_ENVIRONMENT": "test",
            "NOTIFY_HUB_DATABASE_URL": database_url,
            "NOTIFY_HUB_LOG_DIR": str(tmp_path / "logs"),
            "NOTIFY_HUB_MEDIA_ROOT": str(tmp_path / "media"),
        }
    )
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "0017"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    now = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO people "
                "(id, display_name, active, is_default, created_at, updated_at) "
                "VALUES (:id, :name, 1, 0, :now, :now)"
            ),
            {"id": "legacy-person", "name": "Legacy Person", "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO events "
                "(id, source_type, source_id, event_type, event_key, title, content, level, "
                "payload, occurred_at, accepted_at, status) "
                "VALUES (:id, 'api_client', 'legacy-client', 'legacy.event', 'same-key', "
                "'Legacy', 'content', 'info', '{}', :now, :now, 'accepted')"
            ),
            {"id": "legacy-event", "now": now},
        )
    engine.dispose()
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    try:
        inspector = inspect(engine)
        assert "profile_id" in {column["name"] for column in inspector.get_columns("events")}
        with engine.begin() as connection:
            profile = connection.execute(
                text("SELECT id, key FROM application_profiles WHERE id = :id"),
                {"id": DEFAULT_PROFILE_ID},
            ).one()
            assert tuple(profile) == (DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY)
            member = connection.execute(
                text("SELECT person_id FROM profile_members WHERE profile_id = :profile_id"),
                {"profile_id": DEFAULT_PROFILE_ID},
            ).all()
            assert [row[0] for row in member] == ["legacy-person"]
            legacy_event = connection.execute(
                text("SELECT profile_id FROM events WHERE id = 'legacy-event'")
            ).scalar_one()
            assert legacy_event == DEFAULT_PROFILE_ID
            connection.execute(
                text(
                    "INSERT INTO events "
                    "(id, profile_id, source_type, source_id, event_type, event_key, title, "
                    "content, "
                    "level, payload, occurred_at, accepted_at, status) "
                    "VALUES ('profile-event', 'profile-a', 'api_client', 'legacy-client', "
                    "'legacy.event', 'same-key', 'Profile', 'content', 'info', '{}', :now, :now, "
                    "'accepted')"
                ),
                {"now": now},
            )
            count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM events "
                    "WHERE source_type = 'api_client' AND source_id = 'legacy-client' "
                    "AND event_key = 'same-key'"
                )
            ).scalar_one()
            assert count == 2
    finally:
        engine.dispose()
