from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from app.application.reminder_access import (
    ReminderAccessService,
    ReminderActor,
    ReminderPermissions,
)
from app.application.reminder_service import ReminderCreate
from app.domain.reminder_schedules import MisfirePolicy
from app.domain.reminders import AckPolicy, ScheduleType
from app.infrastructure.database.plugin_models import PluginConfig, PluginState
from app.plugin_runtime.context import (
    ConfigStore,
    PluginReminderDraft,
    PluginReminderReceipt,
    SecretResolver,
    StateStore,
    StateValue,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class PluginStateConflictError(RuntimeError):
    pass


class SecretPermissionError(PermissionError):
    pass


class DatabasePluginStateStore(StateStore):
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], clock: Callable[[], datetime]
    ) -> None:
        self._factory = factory
        self._clock = clock

    async def get(self, plugin_id: str, key: str) -> StateValue | None:
        async with self._factory() as session:
            row = await session.get(PluginState, (plugin_id, key))
            return None if row is None else StateValue(row.value, row.version)

    async def set(self, plugin_id: str, key: str, value: Any, expected_version: int | None) -> int:
        async with self._factory() as session, session.begin():
            row = await session.get(PluginState, (plugin_id, key))
            if row is None:
                if expected_version not in {None, 0}:
                    raise PluginStateConflictError("plugin state version changed")
                session.add(
                    PluginState(
                        plugin_id=plugin_id,
                        key=key,
                        value=value,
                        version=1,
                        updated_at=self._clock(),
                    )
                )
                return 1
            if expected_version is not None and row.version != expected_version:
                raise PluginStateConflictError("plugin state version changed")
            old_version = row.version
            result = await session.execute(
                update(PluginState)
                .where(
                    PluginState.plugin_id == plugin_id,
                    PluginState.key == key,
                    PluginState.version == old_version,
                )
                .values(value=value, version=old_version + 1, updated_at=self._clock())
            )
            if int(getattr(result, "rowcount", 0)) != 1:
                raise PluginStateConflictError("plugin state version changed")
            return old_version + 1

    async def save_checkpoint(self, plugin_id: str, values: Mapping[str, Any]) -> None:
        async with self._factory() as session, session.begin():
            now = self._clock()
            for key, value in values.items():
                row = await session.get(PluginState, (plugin_id, key))
                if row is None:
                    session.add(
                        PluginState(
                            plugin_id=plugin_id,
                            key=key,
                            value=value,
                            version=1,
                            updated_at=now,
                        )
                    )
                else:
                    row.value = value
                    row.version += 1
                    row.updated_at = now


class DatabasePluginConfigStore(ConfigStore):
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get(self, plugin_id: str) -> dict[str, Any]:
        async with self._factory() as session:
            row = await session.get(PluginConfig, plugin_id)
            return {} if row is None else dict(row.config)


class AuthorizedSecretResolver(SecretResolver):
    def __init__(self, plugin_id: str, allowed: set[str], delegate: SecretResolver) -> None:
        self._plugin_id = plugin_id
        self._allowed = allowed
        self._delegate = delegate

    async def resolve(self, plugin_id: str, name: str) -> str:
        if plugin_id != self._plugin_id or name not in self._allowed:
            raise SecretPermissionError("plugin secret is not permitted")
        return await self._delegate.resolve(plugin_id, name)


class PluginReminderCreator:
    def __init__(
        self,
        *,
        access: ReminderAccessService,
        plugin_id: str,
        run_id: str,
        permissions: ReminderPermissions,
    ) -> None:
        self._access = access
        self._plugin_id = plugin_id
        self._run_id = run_id
        self._permissions = permissions

    async def create(self, draft: PluginReminderDraft) -> PluginReminderReceipt:
        result = await self._access.create(
            ReminderCreate(
                creator_person_id=draft.creator_person_id,
                title=draft.title,
                content=draft.content,
                schedule_type=ScheduleType(draft.schedule_type),
                timezone=draft.timezone,
                recipient_ids=draft.recipient_ids,
                scheduled_at=draft.scheduled_at,
                recurrence_rule=draft.recurrence_rule,
                interval_seconds=draft.interval_seconds,
                cron_expression=draft.cron_expression,
                start_at=draft.start_at,
                end_at=draft.end_at,
                misfire_policy=MisfirePolicy(draft.misfire_policy),
                require_ack=draft.require_ack,
                ack_policy=AckPolicy(draft.ack_policy),
                repeat_interval_seconds=draft.repeat_interval_seconds,
                max_reminders=draft.max_reminders,
                stop_at=draft.stop_at,
                content_type=draft.content_type,
                media_asset_id=draft.media_asset_id,
                url=draft.url,
            ),
            actor=ReminderActor("plugin", self._plugin_id),
            permissions=self._permissions,
            schedule_mode=draft.schedule_mode,
            idempotency_key=draft.idempotency_key,
            request_id=f"plugin-run:{self._run_id}",
        )
        return PluginReminderReceipt(
            reminder_id=result.reminder.id,
            status=result.reminder.status,
            duplicate=result.duplicate,
        )
