from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.application.plugin_service import PluginService
from app.application.reminder_access import ReminderAccessService
from app.application.reminder_service import EventAcceptance, ReminderEventDraft, ReminderService
from app.domain.clock import SystemClock
from app.infrastructure.database.base import Base
from app.infrastructure.database.models import AuditLog, Person
from app.infrastructure.database.session import create_session_factory
from app.plugin_runtime.base import EventDraft, EventReceipt
from app.plugin_runtime.context import PluginReminderClient
from app.plugin_runtime.manifest import PluginManifest
from app.plugin_runtime.registry import PluginRegistry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_core_api import initialize_and_login


def _reminder_body(**updates: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "Client reminder",
        "content": "created through constrained API",
        "schedule": {"type": "once", "at": "2099-01-01T00:00:00Z"},
        "recipients": ["person_allowed"],
    }
    body.update(updates)
    return body


@pytest.mark.integration
async def test_client_reminder_permissions_quota_and_audit(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = api
    token = await initialize_and_login(client)
    admin_headers = {"Authorization": f"Bearer {token}"}
    for person_id in ("person_allowed", "person_denied"):
        response = await client.post(
            "/api/v1/admin/people",
            headers=admin_headers,
            json={"id": person_id, "display_name": person_id},
        )
        assert response.status_code == 201, response.text

    denied_client = await client.post(
        "/api/v1/admin/api-clients",
        headers=admin_headers,
        json={"name": "no reminders", "allowed_recipient_ids": ["person_allowed"]},
    )
    denied_key = denied_client.json()["data"]["api_key"]
    denied = await client.post(
        "/api/v1/reminders",
        headers={"X-API-Key": denied_key},
        json=_reminder_body(),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "reminder_forbidden"

    created_client = await client.post(
        "/api/v1/admin/api-clients",
        headers=admin_headers,
        json={
            "id": "client_reminders",
            "name": "reminders",
            "allowed_recipient_ids": ["person_allowed"],
            "allow_reminders": True,
            "max_active_reminders": 1,
        },
    )
    assert created_client.status_code == 201, created_client.text
    key = created_client.json()["data"]["api_key"]
    api_headers = {"X-API-Key": key}

    recipient_denied = await client.post(
        "/api/v1/reminders",
        headers=api_headers,
        json=_reminder_body(recipients=["person_denied"]),
    )
    assert recipient_denied.status_code == 403

    accepted = await client.post(
        "/api/v1/reminders",
        headers={**api_headers, "Idempotency-Key": "stable-reminder-1"},
        json=_reminder_body(),
    )
    assert accepted.status_code == 201, accepted.text
    reminder_id = accepted.json()["data"]["id"]
    duplicate = await client.post(
        "/api/v1/reminders",
        headers={**api_headers, "Idempotency-Key": "stable-reminder-1"},
        json=_reminder_body(),
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["data"]["id"] == reminder_id
    assert duplicate.json()["data"]["duplicate"] is True
    conflict = await client.post(
        "/api/v1/reminders",
        headers={**api_headers, "Idempotency-Key": "stable-reminder-1"},
        json=_reminder_body(title="different payload"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    quota = await client.post(
        "/api/v1/reminders",
        headers={**api_headers, "Idempotency-Key": "stable-reminder-2"},
        json=_reminder_body(title="second"),
    )
    assert quota.status_code == 409
    assert quota.json()["error"]["code"] == "reminder_quota_exceeded"

    async with app.state.session_factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "reminder.create",
                AuditLog.actor_type == "api_client",
                AuditLog.actor_id == "client_reminders",
                AuditLog.resource_id == reminder_id,
            )
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_context_and_manifest_default_to_no_reminder_access() -> None:
    manifest = PluginManifest.model_validate(
        {
            "id": "safe_plugin",
            "name": "Safe",
            "version": "1.0.0",
            "entrypoint": "plugin:SafePlugin",
            "api_version": "1",
            "trusted": True,
            "default_schedule": {"type": "interval", "seconds": 60},
        }
    )
    assert manifest.permissions.reminders.create is False
    with pytest.raises(PermissionError, match="does not have reminder"):
        await PluginReminderClient(None).create(
            creator_person_id="person_plugin",
            title="blocked",
            schedule_type="once",
            scheduled_at=datetime(2099, 1, 1, tzinfo=UTC),
            recipient_ids=("person_plugin",),
            idempotency_key="stable-plugin-reminder",
        )


class _NoEvents:
    async def emit(self, plugin_id: str, event: EventDraft) -> EventReceipt:
        raise AssertionError(f"unexpected event from {plugin_id}: {event.event_key}")


class _NoSecrets:
    async def resolve(self, plugin_id: str, name: str) -> str:
        raise AssertionError(f"unexpected secret access: {plugin_id}/{name}")


async def _accept_reminder_event(_draft: ReminderEventDraft) -> EventAcceptance:
    raise AssertionError("creating a future reminder must not emit immediately")


def _write_reminder_plugin(root: Path) -> None:
    directory = root / "reminder_plugin"
    directory.mkdir(parents=True)
    manifest = {
        "id": "reminder_plugin",
        "name": "Reminder Plugin",
        "version": "1.0.0",
        "entrypoint": "plugin:ReminderPlugin",
        "api_version": "1",
        "trusted": True,
        "default_schedule": {"type": "interval", "seconds": 60},
        "permissions": {
            "reminders": {
                "create": True,
                "allowed_recipients": ["person_plugin"],
                "max_active": 1,
            }
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "plugin.py").write_text(
        """
from datetime import UTC, datetime
from types import SimpleNamespace

class ReminderPlugin:
    @classmethod
    def metadata(cls):
        return {"id": "reminder_plugin", "name": "Reminder Plugin", "version": "1.0.0"}

    @classmethod
    def config_schema(cls):
        return {"type": "object", "additionalProperties": False}

    async def run(self, context):
        receipt = await context.reminders.create(
            creator_person_id="person_plugin",
            title="Plugin reminder",
            content="through context.reminders",
            schedule_type="once",
            scheduled_at=datetime(2099, 1, 1, tzinfo=UTC),
            recipient_ids=("person_plugin",),
            idempotency_key="stable-plugin-reminder",
        )
        return SimpleNamespace(status="success", emitted_events=0, message=receipt.reminder_id)
""",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_plugin_context_creates_through_access_service_and_is_audited(tmp_path: Path) -> None:
    plugin_root = tmp_path / "builtin"
    _write_reminder_plugin(plugin_root)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        session.add(
            Person(
                id="person_plugin",
                display_name="Plugin Owner",
                active=True,
                is_default=False,
                created_at=now,
                updated_at=now,
            )
        )

    clock = SystemClock()
    reminder_service = ReminderService(factory, _accept_reminder_event, "test-secret", clock)
    service = PluginService(
        session_factory=factory,
        registry=PluginRegistry({"builtin": plugin_root}),
        event_emitter=_NoEvents(),
        secret_resolver=_NoSecrets(),
        reminder_access=ReminderAccessService(factory, reminder_service, clock),
    )
    await service.initialize()
    await service.update_config("reminder_plugin", {})
    await service.enable("reminder_plugin")
    run_id = await service.queue_manual("reminder_plugin")
    assert await service.claim_next("test-worker") == run_id
    outcome = await service.execute(run_id)
    assert outcome.status == "succeeded"
    second_run_id = await service.queue_manual("reminder_plugin")
    assert await service.claim_next("test-worker") == second_run_id
    second_outcome = await service.execute(second_run_id)
    assert second_outcome.status == "succeeded"

    async with factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.actor_type == "plugin",
                AuditLog.actor_id == "reminder_plugin",
                AuditLog.action == "reminder.create",
            )
        )
        assert audit is not None
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.actor_type == "plugin",
                AuditLog.actor_id == "reminder_plugin",
                AuditLog.action == "reminder.create",
            )
        )
        assert audit_count == 1
    await engine.dispose()
