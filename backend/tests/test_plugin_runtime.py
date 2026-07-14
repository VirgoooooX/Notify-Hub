from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.application.plugin_service import (
    PluginService,
    PluginStateConflictError,
    _DatabaseStateStore,
)
from app.infrastructure.database.base import Base
from app.infrastructure.database.session import create_session_factory
from app.plugin_runtime.base import EventDraft, EventReceipt
from app.plugin_runtime.http import RestrictedHttpClient, RestrictedHttpError
from app.plugin_runtime.manifest import PluginManifest
from app.plugin_runtime.registry import PluginRegistry
from app.plugin_runtime.schedule import next_run_at
from sqlalchemy.ext.asyncio import create_async_engine


class FakeEmitter:
    def __init__(self) -> None:
        self.events: list[EventDraft] = []

    async def emit(self, plugin_id: str, event: EventDraft) -> EventReceipt:
        assert plugin_id == "fake_monitor"
        self.events.append(EventDraft.model_validate(event, from_attributes=True))
        return EventReceipt(event_id="evt_1", status="accepted")


class FakeSecrets:
    async def resolve(self, plugin_id: str, name: str) -> str:
        raise AssertionError(f"unexpected secret access: {plugin_id}/{name}")


def _write_fake_plugin(root: Path) -> None:
    directory = root / "fake_monitor"
    directory.mkdir(parents=True)
    manifest = {
        "id": "fake_monitor",
        "name": "Fake Monitor",
        "version": "1.0.0",
        "description": "test plugin",
        "entrypoint": "plugin:FakePlugin",
        "api_version": "1",
        "kind": "monitor",
        "trusted": True,
        "default_schedule": {"type": "interval", "seconds": 60},
        "max_concurrency": 1,
        "timeout_seconds": 5,
        "permissions": {"network": [], "secrets": []},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "plugin.py").write_text(
        """
from types import SimpleNamespace

class FakePlugin:
    @classmethod
    def metadata(cls):
        return {"id": "fake_monitor", "name": "Fake Monitor", "version": "1.0.0"}

    @classmethod
    def config_schema(cls):
        return {"type": "object", "additionalProperties": False}

    async def run(self, context):
        await context.set_state("cursor", "done")
        await context.emit_event(SimpleNamespace(
            event_type="fake.changed", event_key="item-1", title="Changed", content="",
            level="info", occurred_at=None, url=None, image_url=None, recipients=None,
            require_ack=False, payload={}, article=SimpleNamespace(
                title="Changed", description="", url="https://x.com/post/1", image_url=None
            ),
        ))
        return SimpleNamespace(status="success", emitted_events=1, message=None)
""",
        encoding="utf-8",
    )


def test_manifest_and_interval_schedule() -> None:
    manifest = PluginManifest.model_validate(
        {
            "id": "safe_plugin",
            "name": "Safe",
            "version": "1.2.3",
            "entrypoint": "plugin:SafePlugin",
            "api_version": "1",
            "trusted": True,
            "default_schedule": {"type": "interval", "seconds": 60},
        }
    )
    after = datetime(2026, 1, 1)
    assert (
        next_run_at(manifest.default_schedule, after) - after.replace(tzinfo=UTC)
    ).total_seconds() == 60


@pytest.mark.asyncio
async def test_restricted_http_blocks_loopback() -> None:
    client = RestrictedHttpClient(allowed_hosts=["127.0.0.1"])
    try:
        with pytest.raises(RestrictedHttpError, match="forbidden"):
            await client.validate_url("http://127.0.0.1/private")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_plugin_service_recovers_runs_and_executes_structural_plugin(tmp_path: Path) -> None:
    plugin_root = tmp_path / "builtin"
    _write_fake_plugin(plugin_root)
    registry = PluginRegistry({"builtin": plugin_root})
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    emitter = FakeEmitter()
    service = PluginService(
        session_factory=factory,
        registry=registry,
        event_emitter=emitter,
        secret_resolver=FakeSecrets(),
    )
    await service.initialize()
    await service.update_config("fake_monitor", {})
    await service.enable("fake_monitor")
    run_id = await service.queue_manual("fake_monitor")
    assert await service.claim_next("test-worker") == run_id
    outcome = await service.execute(run_id)
    assert outcome.status == "succeeded"
    assert len(emitter.events) == 1
    assert emitter.events[0].message_type == "article"
    assert await service.get_state("fake_monitor") == {"cursor": {"value": "done", "version": 1}}
    runs = await service.list_runs("fake_monitor")
    assert runs[0]["status"] == "succeeded"
    await engine.dispose()


@pytest.mark.asyncio
async def test_plugin_state_optimistic_lock(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = _DatabaseStateStore(create_session_factory(engine), lambda: datetime.now(UTC))
    assert await store.set("plugin", "cursor", 1, 0) == 1
    with pytest.raises(PluginStateConflictError):
        await store.set("plugin", "cursor", 2, 0)
    assert await store.set("plugin", "cursor", 2, 1) == 2
    await engine.dispose()
