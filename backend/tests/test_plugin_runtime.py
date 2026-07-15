from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai.schemas import AIClassificationItem, AIClassificationResult
from app.application.ai_profile_references import referenced_ai_profiles
from app.application.plugin_service import (
    PluginAIProfileUnavailableError,
    PluginService,
    PluginStateConflictError,
    _DatabaseStateStore,
)
from app.infrastructure.database.base import Base
from app.infrastructure.database.plugin_models import PluginConfig, PluginRecord
from app.infrastructure.database.session import create_session_factory
from app.plugin_runtime.base import EventDraft, EventReceipt
from app.plugin_runtime.context import PluginAIClient
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


def _write_capability_plugin(root: Path) -> None:
    directory = root / "capability_monitor"
    directory.mkdir(parents=True)
    manifest = {
        "id": "capability_monitor",
        "name": "Capability Monitor",
        "version": "1.0.0",
        "entrypoint": "plugin:CapabilityPlugin",
        "api_version": "1",
        "trusted": True,
        "default_schedule": {"type": "interval", "seconds": 60},
        "permissions": {"ai_capabilities": ["classify"]},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "plugin.py").write_text(
        """
class CapabilityPlugin:
    @classmethod
    def metadata(cls):
        return {"id": "capability_monitor", "name": "Capability Monitor", "version": "1.0.0"}

    @classmethod
    def config_schema(cls):
        return {"type": "object"}

    @classmethod
    def validate_config(cls, config):
        if config.get("invalid"):
            raise ValueError("invalid historical configuration")
        return {**config, "ai_profile": config.get("ai_profile", "default_classifier")}

    async def run(self, context):
        raise AssertionError("not executed")
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


def test_cron_uses_standard_day_or_weekday_semantics() -> None:
    manifest = PluginManifest.model_validate(
        {
            "id": "safe_plugin",
            "name": "Safe",
            "version": "1.2.3",
            "entrypoint": "plugin:SafePlugin",
            "api_version": "1",
            "trusted": True,
            "default_schedule": {
                "type": "cron",
                "expression": "0 0 15 * 1",
                "timezone": "UTC",
            },
        }
    )
    # Both day-of-month and weekday are restricted: traditional cron matches either.
    assert next_run_at(
        manifest.default_schedule, datetime(2026, 1, 1, tzinfo=UTC)
    ) == datetime(2026, 1, 5, tzinfo=UTC)


def test_registry_rejects_unusable_default_cron(tmp_path: Path) -> None:
    plugin_root = tmp_path / "builtin"
    _write_fake_plugin(plugin_root)
    manifest_path = plugin_root / "fake_monitor" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["default_schedule"] = {
        "type": "cron",
        "expression": "*/10 * * * *",
        "timezone": "Mars/Olympus",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = PluginRegistry({"builtin": plugin_root})
    assert registry.discover() == []
    assert "unknown timezone" in next(iter(registry.errors.values()))


def test_manifest_capability_permission_references_configured_profile() -> None:
    manifest = PluginManifest.model_validate(
        {
            "id": "safe_plugin",
            "name": "Safe",
            "version": "1.2.3",
            "entrypoint": "plugin:SafePlugin",
            "api_version": "1",
            "trusted": True,
            "default_schedule": {"type": "interval", "seconds": 60},
            "permissions": {"ai_capabilities": ["classify"]},
        }
    )

    assert referenced_ai_profiles(
        manifest,
        {"decision_mode": "ai", "ai_profile": "custom_classifier"},
    ) == {"custom_classifier"}
    assert (
        referenced_ai_profiles(
            manifest,
            {"decision_mode": "rules", "ai_profile": "custom_classifier"},
        )
        == set()
    )


@pytest.mark.asyncio
async def test_plugin_service_validates_selected_profile_capability() -> None:
    manifest = PluginManifest.model_validate(
        {
            "id": "safe_plugin",
            "name": "Safe",
            "version": "1.2.3",
            "entrypoint": "plugin:SafePlugin",
            "api_version": "1",
            "trusted": True,
            "default_schedule": {"type": "interval", "seconds": 60},
            "permissions": {"ai_capabilities": ["classify"]},
        }
    )
    session = AsyncMock()
    session.scalars.return_value = [SimpleNamespace(id="selected_profile", capability="classify")]

    await PluginService._ensure_ai_profiles_available(
        session,
        manifest,
        {"decision_mode": "ai", "ai_profile": "selected_profile"},
    )

    session.scalars.return_value = [SimpleNamespace(id="selected_profile", capability="summarize")]
    with pytest.raises(PluginAIProfileUnavailableError, match="selected_profile"):
        await PluginService._ensure_ai_profiles_available(
            session,
            manifest,
            {"decision_mode": "ai", "ai_profile": "selected_profile"},
        )


class FakeAIService:
    async def classify_many(self, **kwargs: object) -> list[AIClassificationResult]:
        items = kwargs["items"]
        assert isinstance(items, list)
        return [
            AIClassificationResult(id=item.id, label="ignore", confidence=1, reason="test")
            for item in items
        ]


@pytest.mark.asyncio
async def test_plugin_ai_client_enforces_manifest_profiles() -> None:
    client = PluginAIClient(
        plugin_id="fake_monitor",
        run_id="run-1",
        allowed_profiles={"allowed_profile"},
        service=FakeAIService(),  # type: ignore[arg-type]
    )
    with pytest.raises(PermissionError, match="not permitted"):
        await client.classify_many(
            profile="forbidden_profile",
            use_case="test",
            instruction="classify",
            labels=["notify", "ignore"],
            items=[AIClassificationItem(id="1", content="test")],
        )
    results = await client.classify_many(
        profile="allowed_profile",
        use_case="test",
        instruction="classify",
        labels=["notify", "ignore"],
        items=[AIClassificationItem(id="1", content="test")],
    )
    assert results[0].label == "ignore"


@pytest.mark.asyncio
async def test_plugin_ai_client_only_accepts_resolved_runtime_profile() -> None:
    client = PluginAIClient(
        plugin_id="fake_monitor",
        run_id="run-1",
        allowed_profiles={"administrator_selected_profile"},
        service=FakeAIService(),  # type: ignore[arg-type]
    )

    results = await client.classify_many(
        profile="administrator_selected_profile",
        use_case="test",
        instruction="classify",
        labels=["notify", "ignore"],
        items=[AIClassificationItem(id="1", content="test")],
    )

    assert results[0].label == "ignore"
    with pytest.raises(PermissionError, match="not permitted"):
        await client.classify_many(
            profile="different_classifier",
            use_case="test",
            instruction="classify",
            labels=["notify", "ignore"],
            items=[AIClassificationItem(id="1", content="test")],
        )


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
async def test_initialize_materializes_profile_default_for_enabled_plugin(tmp_path: Path) -> None:
    plugin_root = tmp_path / "builtin"
    _write_capability_plugin(plugin_root)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    first = PluginService(
        session_factory=factory,
        registry=PluginRegistry({"builtin": plugin_root}),
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await first.initialize()
    async with factory() as session, session.begin():
        plugin = await session.get(PluginRecord, "capability_monitor")
        assert plugin is not None
        plugin.enabled = True
        session.add(
            PluginConfig(
                plugin_id="capability_monitor",
                config={"decision_mode": "ai"},
                schema_version=1,
                updated_at=datetime.now(UTC),
            )
        )

    restarted = PluginService(
        session_factory=factory,
        registry=PluginRegistry({"builtin": plugin_root}),
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await restarted.initialize()

    async with factory() as session:
        config = await session.get(PluginConfig, "capability_monitor")
        assert config is not None
        assert config.config["ai_profile"] == "default_classifier"

    async with factory() as session, session.begin():
        plugin = await session.get(PluginRecord, "capability_monitor")
        config = await session.get(PluginConfig, "capability_monitor")
        assert plugin is not None and config is not None
        plugin.enabled = True
        config.config = {"decision_mode": "ai", "invalid": True}

    isolated_restart = PluginService(
        session_factory=factory,
        registry=PluginRegistry({"builtin": plugin_root}),
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await isolated_restart.initialize()

    async with factory() as session:
        plugin = await session.get(PluginRecord, "capability_monitor")
        config = await session.get(PluginConfig, "capability_monitor")
        assert plugin is not None and config is not None
        assert plugin.enabled is False
        assert plugin.status == "failed"
        assert "incompatible" in (plugin.last_error or "")
        assert config.config == {"decision_mode": "ai", "invalid": True}
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


@pytest.mark.asyncio
async def test_plugin_service_updates_schedule_on_initialize(tmp_path: Path) -> None:
    plugin_root = tmp_path / "builtin"
    plugin_dir = plugin_root / "fake_monitor"
    _write_fake_plugin(plugin_root)

    registry = PluginRegistry({"builtin": plugin_root})
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    # Initialize the first time to create the record
    service = PluginService(
        session_factory=factory,
        registry=registry,
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await service.initialize()

    # Enable the plugin so next_run_at is computed
    await service.enable("fake_monitor")

    # Verify initial schedule is 60s
    async with factory() as session:
        row = await session.get(PluginRecord, "fake_monitor")
        assert row is not None
        assert row.schedule == {"type": "interval", "seconds": 60}
        assert row.schedule_inherits_default is True
        initial_next_run = row.next_run_at

    # Simulate an existing row immediately after the nullable migration is applied.
    async with factory() as session, session.begin():
        row = await session.get(PluginRecord, "fake_monitor")
        assert row is not None
        row.schedule_inherits_default = None

    # 2. Modify the manifest to have a new default schedule (interval of 120s)
    manifest_path = plugin_dir / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["default_schedule"] = {"type": "interval", "seconds": 120}
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    # Re-initialize the plugin service
    registry_new = PluginRegistry({"builtin": plugin_root})
    service_new = PluginService(
        session_factory=factory,
        registry=registry_new,
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await service_new.initialize()

    # Verify that the schedule and next_run_at were updated automatically
    async with factory() as session:
        row = await session.get(PluginRecord, "fake_monitor")
        assert row is not None
        assert row.schedule == {"type": "interval", "seconds": 120}
        assert row.schedule_inherits_default is True
        assert row.next_run_at != initial_next_run

    # 3. Test that custom schedules are preserved
    custom_schedule = {"type": "interval", "seconds": 300}
    await service_new.update_config("fake_monitor", {}, schedule=custom_schedule)
    await service_new.update_config("fake_monitor", {})

    # Modify the manifest again to 240s
    manifest_data["default_schedule"] = {"type": "interval", "seconds": 240}
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    registry_new_2 = PluginRegistry({"builtin": plugin_root})
    service_new_2 = PluginService(
        session_factory=factory,
        registry=registry_new_2,
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await service_new_2.initialize()

    # Verify that the custom schedule of 300s is preserved
    async with factory() as session:
        row = await session.get(PluginRecord, "fake_monitor")
        assert row is not None
        assert row.schedule == {"type": "interval", "seconds": 300}
        assert row.schedule_inherits_default is False

    # 4. Resetting opts back into future Manifest default changes.
    await service_new_2.reset_schedule("fake_monitor")
    manifest_data["default_schedule"] = {"type": "interval", "seconds": 360}
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
    service_new_3 = PluginService(
        session_factory=factory,
        registry=PluginRegistry({"builtin": plugin_root}),
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await service_new_3.initialize()
    async with factory() as session:
        row = await session.get(PluginRecord, "fake_monitor")
        assert row is not None
        assert row.schedule == {"type": "interval", "seconds": 360}
        assert row.schedule_inherits_default is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_plugin_service_validates_custom_cron_timezone(tmp_path: Path) -> None:
    plugin_root = tmp_path / "builtin"
    _write_fake_plugin(plugin_root)
    registry = PluginRegistry({"builtin": plugin_root})
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    service = PluginService(
        session_factory=factory,
        registry=registry,
        event_emitter=FakeEmitter(),
        secret_resolver=FakeSecrets(),
    )
    await service.initialize()

    with pytest.raises(ValueError, match="timezone"):
        await service.update_schedule(
            "fake_monitor",
            {"type": "cron", "expression": "*/10 * * * *", "timezone": "Mars/Olympus"},
        )

    await service.update_schedule(
        "fake_monitor",
        {"type": "cron", "expression": "*/10 * * * *", "timezone": "Asia/Shanghai"},
    )
    await service.update_config("fake_monitor", {})
    async with factory() as session:
        row = await session.get(PluginRecord, "fake_monitor")
        assert row is not None
        assert row.schedule == {
            "type": "cron",
            "expression": "*/10 * * * *",
            "timezone": "Asia/Shanghai",
        }
        assert row.schedule_inherits_default is False

    await engine.dispose()
