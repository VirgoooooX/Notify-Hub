from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from app.application.ai_profile_references import referenced_ai_profiles
from app.application.media_service import MediaService
from app.application.plugin_runtime_adapters import (
    AuthorizedSecretResolver,
    DatabasePluginConfigStore,
    DatabasePluginStateStore,
    PluginReminderCreator,
    PluginStateConflictError,
    SecretPermissionError,
)
from app.application.reminder_access import ReminderAccessService, ReminderPermissions
from app.config import Settings
from app.infrastructure.database.ai_models import AIProfile
from app.infrastructure.database.base import new_id
from app.infrastructure.database.plugin_models import (
    PluginConfig,
    PluginRecord,
    PluginRun,
    PluginState,
)
from app.media.public_urls import PublicMediaUrlBuilder
from app.plugin_runtime.base import EventDraft, EventReceipt
from app.plugin_runtime.context import (
    EventEmitter,
    PluginAIClient,
    PluginContext,
    PluginReminderClient,
    SecretResolver,
)
from app.plugin_runtime.http import RestrictedHttpClient
from app.plugin_runtime.manifest import PluginManifest, PluginSchedule
from app.plugin_runtime.registry import PluginLoadError, PluginRegistry
from app.plugin_runtime.runner import PluginRunner, RunOutcome
from app.plugin_runtime.schedule import next_run_at
from app.plugin_runtime.schema import validate_json_schema
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Compatibility aliases for existing callers while runtime adapters live in their own module.
_DatabaseStateStore = DatabasePluginStateStore
_DatabaseConfigStore = DatabasePluginConfigStore
_AuthorizedSecretResolver = AuthorizedSecretResolver
_PluginReminderCreator = PluginReminderCreator

__all__ = [
    "EventServiceEmitter",
    "PluginAIProfileUnavailableError",
    "PluginNotFoundError",
    "PluginRunConflictError",
    "PluginService",
    "PluginStateConflictError",
    "SecretPermissionError",
]


class PluginNotFoundError(LookupError):
    pass


class PluginRunConflictError(RuntimeError):
    pass


class PluginAIProfileUnavailableError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class PluginService:
    DEGRADED_AFTER = 5
    CIRCUIT_OPEN_AFTER = 10

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        registry: PluginRegistry,
        event_emitter: EventEmitter,
        secret_resolver: SecretResolver,
        runner: PluginRunner | None = None,
        clock: Callable[[], datetime] = utcnow,
        media_service: MediaService | None = None,
        settings: Settings | None = None,
        ai_service: Any = None,
        reminder_access: ReminderAccessService | None = None,
    ) -> None:
        self._factory = session_factory
        self.registry = registry
        self._emitter = event_emitter
        self._secrets = secret_resolver
        self._runner = runner or PluginRunner()
        self._clock = clock
        self._state = DatabasePluginStateStore(session_factory, clock)
        self._config = DatabasePluginConfigStore(session_factory)
        self._media_service = media_service
        self._settings = settings
        self._ai_service = ai_service
        self._reminder_access = reminder_access

    async def initialize(self) -> None:
        self.registry.discover()
        now = self._clock()
        async with self._factory() as session, session.begin():
            for registered in self.registry.list():
                manifest = registered.manifest
                row = await session.get(PluginRecord, manifest.id)
                if row is None:
                    session.add(
                        PluginRecord(
                            id=manifest.id,
                            name=manifest.name,
                            version=manifest.version,
                            description=manifest.description,
                            install_type=registered.install_type,
                            enabled=False,
                            status="disabled",
                            consecutive_failures=0,
                            circuit_open=False,
                            last_run_at=None,
                            next_run_at=None,
                            last_error=None,
                            manifest=manifest.model_dump(mode="json"),
                            schedule=manifest.default_schedule.model_dump(mode="json"),
                            schedule_inherits_default=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    old_manifest = row.manifest
                    if row.schedule_inherits_default is None and isinstance(old_manifest, dict):
                        old_default = old_manifest.get("default_schedule")
                        row.schedule_inherits_default = (
                            old_default is not None and row.schedule == old_default
                        )
                    if row.schedule_inherits_default:
                        row.schedule = manifest.default_schedule.model_dump(mode="json")
                        if row.enabled and not row.circuit_open:
                            row.next_run_at = next_run_at(manifest.default_schedule, now)
                    row.name = manifest.name
                    row.version = manifest.version
                    row.description = manifest.description
                    row.install_type = registered.install_type
                    row.manifest = manifest.model_dump(mode="json")
                    row.updated_at = now
                    if manifest.permissions.ai_capabilities:
                        config_row = await session.get(PluginConfig, manifest.id)
                        if config_row is not None:
                            try:
                                config_row.config = self._validate_config(
                                    registered.plugin_class, config_row.config
                                )
                                config_row.updated_at = now
                            except Exception:  # Plugin validation is an isolation boundary.
                                row.enabled = False
                                row.status = "failed"
                                row.next_run_at = None
                                row.last_error = (
                                    "plugin configuration is incompatible with the current schema"
                                )
            stale = await session.scalars(select(PluginRun).where(PluginRun.status == "running"))
            for run in stale:
                run.status = "queued"
                run.started_at = None
                run.worker_id = None

    async def list_plugins(self) -> list[dict[str, Any]]:
        async with self._factory() as session:
            rows = await session.scalars(select(PluginRecord).order_by(PluginRecord.id))
            return [self._plugin_dict(row) for row in rows]

    async def get_plugin(self, plugin_id: str) -> dict[str, Any]:
        async with self._factory() as session:
            row = await session.get(PluginRecord, plugin_id)
            if row is None:
                raise PluginNotFoundError(plugin_id)
            result = self._plugin_dict(row)
            config = await session.get(PluginConfig, plugin_id)
            result["config"] = {} if config is None else config.config
            return result

    def _plugin_dict(self, row: PluginRecord) -> dict[str, Any]:
        last_run = row.last_run_at
        if last_run is not None and last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=UTC)

        next_run = row.next_run_at
        if next_run is not None and next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=UTC)

        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "enabled": row.enabled,
            "status": row.status,
            "consecutive_failures": row.consecutive_failures,
            "circuit_open": row.circuit_open,
            "last_run_at": last_run,
            "next_run_at": next_run,
            "last_error": row.last_error,
            "manifest": row.manifest,
            "schedule": row.schedule,
            "schedule_inherits_default": row.schedule_inherits_default is True,
        }

    async def update_config(
        self,
        plugin_id: str,
        config: Mapping[str, Any],
        *,
        schedule: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        registered = self.registry.get(plugin_id)
        validated = self._validate_config(registered.plugin_class, config)
        validated_schedule = (
            None
            if schedule is None
            else PluginManifest.model_validate(
                {**registered.manifest.model_dump(), "default_schedule": schedule}
            ).default_schedule
        )
        now = self._clock()
        schedule_next_run = (
            None if validated_schedule is None else next_run_at(validated_schedule, now)
        )
        async with self._factory() as session, session.begin():
            plugin = await session.get(PluginRecord, plugin_id)
            if plugin is None:
                raise PluginNotFoundError(plugin_id)
            await self._ensure_ai_profiles_available(session, registered.manifest, validated)
            row = await session.get(PluginConfig, plugin_id)
            if row is None:
                session.add(
                    PluginConfig(
                        plugin_id=plugin_id,
                        config=validated,
                        schema_version=1,
                        updated_at=now,
                    )
                )
            else:
                row.config = validated
                row.updated_at = now
            if validated_schedule is not None:
                plugin.schedule = validated_schedule.model_dump(mode="json")
                plugin.schedule_inherits_default = False
            plugin.updated_at = now
            if validated_schedule is not None and plugin.enabled and not plugin.circuit_open:
                plugin.next_run_at = schedule_next_run
        return validated

    async def update_schedule(self, plugin_id: str, schedule: Mapping[str, Any]) -> dict[str, Any]:
        registered = self.registry.get(plugin_id)
        validated_schedule = PluginManifest.model_validate(
            {**registered.manifest.model_dump(), "default_schedule": schedule}
        ).default_schedule
        now = self._clock()
        candidate = next_run_at(validated_schedule, now)
        async with self._factory() as session, session.begin():
            plugin = await session.get(PluginRecord, plugin_id)
            if plugin is None:
                raise PluginNotFoundError(plugin_id)
            plugin.schedule = validated_schedule.model_dump(mode="json")
            plugin.schedule_inherits_default = False
            plugin.updated_at = now
            if plugin.enabled and not plugin.circuit_open:
                plugin.next_run_at = candidate
        return validated_schedule.model_dump(mode="json")

    async def reset_schedule(self, plugin_id: str) -> dict[str, Any]:
        registered = self.registry.get(plugin_id)
        schedule = registered.manifest.default_schedule
        now = self._clock()
        candidate = next_run_at(schedule, now)
        async with self._factory() as session, session.begin():
            plugin = await session.get(PluginRecord, plugin_id)
            if plugin is None:
                raise PluginNotFoundError(plugin_id)
            plugin.schedule = schedule.model_dump(mode="json")
            plugin.schedule_inherits_default = True
            plugin.updated_at = now
            if plugin.enabled and not plugin.circuit_open:
                plugin.next_run_at = candidate
        return schedule.model_dump(mode="json")

    async def enable(self, plugin_id: str) -> None:
        try:
            registered = self.registry.get(plugin_id)
        except PluginLoadError:
            registered = None
        now = self._clock()
        async with self._factory() as session, session.begin():
            row = await session.get(PluginRecord, plugin_id)
            if row is None:
                raise PluginNotFoundError(plugin_id)
            config_row = await session.get(PluginConfig, plugin_id)
            raw_config = {} if config_row is None else dict(config_row.config)
            validated_config = (
                raw_config
                if registered is None
                else self._validate_config(registered.plugin_class, raw_config)
            )
            await self._ensure_ai_profiles_available(
                session,
                PluginManifest.model_validate(row.manifest),
                validated_config,
            )
            if config_row is None:
                session.add(
                    PluginConfig(
                        plugin_id=plugin_id,
                        config=validated_config,
                        schema_version=1,
                        updated_at=now,
                    )
                )
            else:
                config_row.config = validated_config
                config_row.updated_at = now
            row.enabled = True
            row.status = "healthy"
            row.circuit_open = False
            row.consecutive_failures = 0
            row.last_error = None
            row.next_run_at = next_run_at(self._schedule(row), now)
            row.updated_at = now

    @staticmethod
    def _validate_config(plugin_class: type[Any], config: Mapping[str, Any]) -> dict[str, Any]:
        validator = getattr(plugin_class, "validate_config", None)
        if validator is not None:
            return dict(validator(config))
        validated = dict(config)
        validate_json_schema(validated, plugin_class.config_schema())
        return validated

    @staticmethod
    async def _ensure_ai_profiles_available(
        session: AsyncSession,
        manifest: PluginManifest,
        config: Mapping[str, Any],
    ) -> None:
        referenced = referenced_ai_profiles(manifest, config)
        if not referenced:
            return
        profile_rows = list(
            await session.scalars(
                select(AIProfile).where(
                    AIProfile.id.in_(referenced),
                    AIProfile.enabled.is_(True),
                    AIProfile.deleted_at.is_(None),
                )
            )
        )
        allowed_ids = set(manifest.permissions.ai_profiles)
        allowed_capabilities = set(manifest.permissions.ai_capabilities)
        available = {
            profile.id
            for profile in profile_rows
            if profile.id in allowed_ids or profile.capability in allowed_capabilities
        }
        missing = sorted(referenced - available)
        if missing:
            raise PluginAIProfileUnavailableError(
                f"AI profiles are unavailable: {', '.join(missing)}"
            )

    async def disable(self, plugin_id: str) -> None:
        async with self._factory() as session, session.begin():
            row = await session.get(PluginRecord, plugin_id)
            if row is None:
                raise PluginNotFoundError(plugin_id)
            row.enabled = False
            row.status = "disabled"
            row.next_run_at = None
            row.updated_at = self._clock()

    async def queue_manual(self, plugin_id: str, trace_id: str | None = None) -> str:
        return await self._queue(plugin_id, "manual", trace_id)

    async def _queue(self, plugin_id: str, trigger: str, trace_id: str | None) -> str:
        now = self._clock()
        async with self._factory() as session, session.begin():
            if await session.get(PluginRecord, plugin_id) is None:
                raise PluginNotFoundError(plugin_id)
            existing = await session.scalar(
                select(PluginRun.id).where(
                    PluginRun.plugin_id == plugin_id,
                    PluginRun.status.in_(["queued", "running"]),
                )
            )
            if existing is not None:
                raise PluginRunConflictError("plugin already has a queued or running run")
            run_id = new_id("prun")
            session.add(
                PluginRun(
                    id=run_id,
                    plugin_id=plugin_id,
                    trigger_type=trigger,
                    status="queued",
                    created_at=now,
                    emitted_event_count=0,
                    trace_id=trace_id,
                )
            )
            return run_id

    async def enqueue_due(self, limit: int = 50) -> int:
        now = self._clock()
        count = 0
        async with self._factory() as session, session.begin():
            rows = await session.scalars(
                select(PluginRecord)
                .where(
                    PluginRecord.enabled.is_(True),
                    PluginRecord.circuit_open.is_(False),
                    PluginRecord.next_run_at <= now,
                )
                .order_by(PluginRecord.next_run_at)
                .limit(limit)
            )
            for plugin in rows:
                active = await session.scalar(
                    select(PluginRun.id).where(
                        PluginRun.plugin_id == plugin.id,
                        PluginRun.status.in_(["queued", "running"]),
                    )
                )
                if active is None:
                    session.add(
                        PluginRun(
                            id=new_id("prun"),
                            plugin_id=plugin.id,
                            trigger_type="schedule",
                            status="queued",
                            created_at=now,
                            emitted_event_count=0,
                        )
                    )
                    count += 1
                plugin.next_run_at = next_run_at(self._schedule(plugin), now)
        return count

    async def claim_next(self, worker_id: str) -> str | None:
        async with self._factory() as session, session.begin():
            candidates = await session.scalars(
                select(PluginRun)
                .where(PluginRun.status == "queued")
                .order_by(PluginRun.created_at)
                .limit(20)
            )
            for run in candidates:
                running = await session.scalar(
                    select(PluginRun.id).where(
                        PluginRun.plugin_id == run.plugin_id,
                        PluginRun.status == "running",
                    )
                )
                if running is not None:
                    continue
                result = await session.execute(
                    update(PluginRun)
                    .where(PluginRun.id == run.id, PluginRun.status == "queued")
                    .values(status="running", started_at=self._clock(), worker_id=worker_id)
                )
                if int(getattr(result, "rowcount", 0)) == 1:
                    return run.id
        return None

    async def execute(self, run_id: str) -> RunOutcome:
        async with self._factory() as session:
            run = await session.get(PluginRun, run_id)
            if run is None or run.status != "running" or run.started_at is None:
                raise PluginRunConflictError("run is not claimed")
            plugin_id = run.plugin_id
            trigger_type = run.trigger_type
            started_at = run.started_at
            plugin_row = await session.get(PluginRecord, plugin_id)
            if plugin_row is None:
                raise PluginNotFoundError(plugin_id)
            manifest = PluginManifest.model_validate(plugin_row.manifest)
        registered = self.registry.get(plugin_id)
        config = self._validate_config(registered.plugin_class, await self._config.get(plugin_id))
        runtime_profiles = set(manifest.permissions.ai_profiles)
        runtime_profiles.update(referenced_ai_profiles(manifest, config))
        http = RestrictedHttpClient(
            allowed_hosts=manifest.permissions.network,
            allowed_private_networks=manifest.permissions.private_network,
        )
        from app.plugin_runtime.context import PluginMediaPublisher

        key_str = "development-only-change-me-public-media-signing-key"
        if self._settings and self._settings.public_media_signing_key:
            key_str = self._settings.public_media_signing_key.get_secret_value()

        media_publisher = PluginMediaPublisher(
            plugin_id=plugin_id,
            media_write_allowed=manifest.permissions.media_write,
            media_service=self._media_service,
            session_factory=self._factory,
            public_media_urls=PublicMediaUrlBuilder(
                self._settings.public_base_url if self._settings else None,
                key_str,
            ),
        )
        context = PluginContext(
            plugin_id=plugin_id,
            run_id=run_id,
            state=self._state,
            config=self._config,
            emitter=self._emitter,
            secrets=AuthorizedSecretResolver(
                plugin_id, set(manifest.permissions.secrets), self._secrets
            ),
            http=http,
            media=media_publisher,
            ai=PluginAIClient(
                plugin_id=plugin_id,
                run_id=run_id,
                allowed_profiles=runtime_profiles,
                service=self._ai_service,
            ),
            reminders=PluginReminderClient(
                PluginReminderCreator(
                    access=self._reminder_access,
                    plugin_id=plugin_id,
                    run_id=run_id,
                    permissions=ReminderPermissions(
                        allow_create=manifest.permissions.reminders.create,
                        allow_recurring=manifest.permissions.reminders.allow_recurring,
                        allow_cron=manifest.permissions.reminders.allow_cron,
                        allow_interactive=manifest.permissions.reminders.allow_interactive,
                        allow_media=manifest.permissions.reminders.allow_media,
                        allowed_recipients=tuple(manifest.permissions.reminders.allowed_recipients),
                        max_active=manifest.permissions.reminders.max_active,
                        min_interval_seconds=(manifest.permissions.reminders.min_interval_seconds),
                        max_duration_seconds=(manifest.permissions.reminders.max_duration_seconds),
                        max_notifications=manifest.permissions.reminders.max_notifications,
                    ),
                )
                if self._reminder_access is not None and manifest.permissions.reminders.create
                else None
            ),
        )
        try:
            outcome = await self._runner.run(
                run_id=run_id,
                plugin_id=plugin_id,
                plugin=registered.plugin_class(),
                context=context,
                timeout_seconds=manifest.timeout_seconds,
            )
        finally:
            await http.aclose()
        await self._finish(run_id, plugin_id, trigger_type, started_at, outcome)
        return outcome

    async def _finish(
        self,
        run_id: str,
        plugin_id: str,
        trigger_type: str,
        started_at: datetime,
        outcome: RunOutcome,
    ) -> None:
        now = self._clock()
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        async with self._factory() as session, session.begin():
            run = await session.get(PluginRun, run_id)
            plugin = await session.get(PluginRecord, plugin_id)
            if run is None or plugin is None:
                raise PluginNotFoundError(plugin_id)
            run.status = outcome.status
            run.finished_at = now
            run.duration_ms = max(0, int((now - started_at).total_seconds() * 1000))
            run.emitted_event_count = outcome.result.emitted_events if outcome.result else 0
            run.error_type = outcome.error_type
            run.error_message = outcome.error_message
            plugin.last_run_at = now
            plugin.updated_at = now
            if outcome.status == "succeeded":
                plugin.consecutive_failures = 0
                plugin.last_error = None
                plugin.status = "healthy" if plugin.enabled else "disabled"
                if trigger_type == "manual" and plugin.enabled:
                    plugin.circuit_open = False
                    plugin.next_run_at = next_run_at(self._schedule(plugin), now)
            elif outcome.status != "cancelled":
                plugin.consecutive_failures += 1
                plugin.last_error = outcome.error_message
                if plugin.consecutive_failures >= self.CIRCUIT_OPEN_AFTER:
                    plugin.status = "failed"
                    plugin.circuit_open = True
                    plugin.next_run_at = None
                elif plugin.consecutive_failures >= self.DEGRADED_AFTER:
                    plugin.status = "degraded"

    async def cancel(self, run_id: str) -> bool:
        if self._runner.cancel(run_id):
            return True
        async with self._factory() as session, session.begin():
            result = await session.execute(
                update(PluginRun)
                .where(PluginRun.id == run_id, PluginRun.status == "queued")
                .values(status="cancelled", finished_at=self._clock())
            )
            return int(getattr(result, "rowcount", 0)) == 1

    async def list_runs(self, plugin_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._factory() as session:
            rows = await session.scalars(
                select(PluginRun)
                .where(PluginRun.plugin_id == plugin_id)
                .order_by(PluginRun.created_at.desc())
                .limit(limit)
            )
            return [
                {
                    "id": row.id,
                    "plugin_id": row.plugin_id,
                    "trigger_type": row.trigger_type,
                    "status": row.status,
                    "created_at": row.created_at,
                    "started_at": row.started_at,
                    "finished_at": row.finished_at,
                    "duration_ms": row.duration_ms,
                    "emitted_event_count": row.emitted_event_count,
                    "error_type": row.error_type,
                    "error_message": row.error_message,
                }
                for row in rows
            ]

    async def get_state(self, plugin_id: str) -> dict[str, dict[str, Any]]:
        async with self._factory() as session:
            rows = await session.scalars(
                select(PluginState).where(PluginState.plugin_id == plugin_id)
            )
            return {row.key: {"value": row.value, "version": row.version} for row in rows}

    @staticmethod
    def _schedule(plugin: PluginRecord) -> PluginSchedule:
        manifest = PluginManifest.model_validate(
            {**plugin.manifest, "default_schedule": plugin.schedule}
        )
        return manifest.default_schedule


class EventServiceEmitter(EventEmitter):
    """Small adapter for the core EventService without exposing it to plugins."""

    def __init__(self, accept: Callable[[str, EventDraft], Any]) -> None:
        self._accept = accept

    async def emit(self, plugin_id: str, event: EventDraft) -> EventReceipt:
        normalized = EventDraft.model_validate(event, from_attributes=True)
        result = await self._accept(plugin_id, normalized)
        if isinstance(result, EventReceipt):
            return result
        return EventReceipt.model_validate(result)
