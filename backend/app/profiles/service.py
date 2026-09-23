from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.clock import Clock, SystemClock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import (
    ApiClient,
    Delivery,
    Event,
    MpArticle,
    Notification,
    Person,
    Secret,
)
from app.infrastructure.database.plugin_models import PluginRecord
from app.infrastructure.database.profile_models import (
    ApplicationProfile,
    ProfileMember,
    WeComProfileConfig,
)
from app.infrastructure.database.reminder_draft_models import ReminderDraft
from app.infrastructure.database.reminder_models import (
    ConversationSession,
    IncomingMessage,
    InteractionEvent,
    NotificationAction,
    Reminder,
    ReminderOccurrence,
)
from app.profiles.constants import DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY
from app.profiles.errors import ProfileError, ProfileNotFound
from app.profiles.registry import ProfileRegistry
from app.profiles.types import DEFAULT_CAPABILITIES, ProfileCapabilities, ProfileMetadata

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class ProfileCreate:
    key: str
    name: str
    enabled: bool = True
    capabilities: dict[str, bool] | None = None
    agent_id: int | None = None
    wecom_secret: str | None = None
    callback_token: str | None = None
    callback_aes_key: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileUpdate:
    name: str | None = None
    enabled: bool | None = None
    capabilities: dict[str, bool] | None = None


@dataclass(frozen=True, slots=True)
class WeComProfileUpdate:
    agent_id: int | None = None
    enabled: bool | None = None
    callback_enabled: bool | None = None
    wecom_secret: str | None = None
    callback_token: str | None = None
    callback_aes_key: str | None = None


class ApplicationProfileService:
    """Admin-facing profile and membership writes with explicit invariants."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        registry: ProfileRegistry | None = None,
        secret_store: Any = None,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._secret_store = secret_store
        self._clock = clock or SystemClock()

    async def create(self, command: ProfileCreate) -> ProfileMetadata:
        key = command.key.strip()
        name = command.name.strip()
        if key in {DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY}:
            raise ProfileError("notify-hub is reserved as the system default profile")
        if not KEY_PATTERN.fullmatch(key):
            raise ProfileError("profile key must be a lowercase kebab-case identifier")
        if not name:
            raise ProfileError("profile name is required")
        if command.agent_id is not None and command.agent_id < 1:
            raise ProfileError("WeCom Agent ID must be positive")
        now = self._clock.now()
        capabilities = ProfileCapabilities.from_mapping(
            {**DEFAULT_CAPABILITIES, **(command.capabilities or {})}
        )
        profile = ApplicationProfile(
            id=new_id("profile"),
            key=key,
            name=name,
            enabled=command.enabled,
            is_default=False,
            capabilities=capabilities.as_dict(),
            created_at=now,
            updated_at=now,
        )
        async with self._sessions() as session, session.begin():
            if await session.scalar(
                select(ApplicationProfile.id).where(ApplicationProfile.key == key)
            ):
                raise ProfileError("profile key is already in use")
            session.add(profile)
            if command.agent_id is not None or any(
                value is not None for value in (command.callback_token, command.callback_aes_key)
            ):
                session.add(
                    WeComProfileConfig(
                        profile_id=profile.id,
                        agent_id=command.agent_id,
                        enabled=command.enabled,
                        callback_enabled=capabilities.callback_enabled,
                        created_at=now,
                        updated_at=now,
                    )
                )
        await self._put_secrets(
            profile.id,
            wecom_secret=command.wecom_secret,
            callback_token=command.callback_token,
            callback_aes_key=command.callback_aes_key,
        )
        return ProfileRegistry._metadata(profile)

    async def update(self, profile_id: str, command: ProfileUpdate) -> ProfileMetadata:
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            profile = await self._get(session, profile_id)
            is_system_default = profile.id == DEFAULT_PROFILE_ID
            if command.enabled is False and is_system_default:
                raise ProfileError("the default profile cannot be disabled")
            if command.name is not None:
                if not command.name.strip():
                    raise ProfileError("profile name is required")
                profile.name = command.name.strip()
            if command.enabled is not None:
                profile.enabled = command.enabled
            if is_system_default:
                await session.execute(
                    update(ApplicationProfile)
                    .where(ApplicationProfile.id != profile.id)
                    .values(is_default=False, updated_at=now)
                )
                profile.is_default = True
            else:
                profile.is_default = False
            if command.capabilities is not None:
                profile.capabilities = ProfileCapabilities.from_mapping(
                    {**profile.capabilities, **command.capabilities}
                ).as_dict()
            profile.updated_at = now
            metadata = ProfileRegistry._metadata(profile)
        if self._registry is not None:
            await self._registry.invalidate(profile_id)
        return metadata

    async def delete(self, profile_id: str) -> None:
        async with self._sessions() as session, session.begin():
            profile = await self._get(session, profile_id)
            if profile.id == DEFAULT_PROFILE_ID:
                raise ProfileError("the default profile cannot be deleted")
            dependent_tables = (
                ApiClient,
                Event,
                Notification,
                Delivery,
                Reminder,
                ReminderOccurrence,
                IncomingMessage,
                InteractionEvent,
                ConversationSession,
                NotificationAction,
                ReminderDraft,
                PluginRecord,
                MpArticle,
            )
            for model in dependent_tables:
                if hasattr(model, "profile_id"):
                    count = await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.profile_id == profile_id)
                    )
                else:
                    count = 0
                if count:
                    raise ProfileError("profile has dependent records and can only be disabled")
            await session.execute(
                delete(Secret).where(
                    Secret.scope_type == "application_profile",
                    Secret.scope_id == profile_id,
                )
            )
            await session.delete(profile)
        if self._registry is not None:
            await self._registry.invalidate(profile_id)

    async def configure_wecom(
        self, profile_id: str, command: WeComProfileUpdate
    ) -> dict[str, object]:
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            profile = await self._get(session, profile_id)
            config = await session.get(WeComProfileConfig, profile.id)
            if config is None:
                callback_only = any(
                    value is not None
                    for value in (
                        command.callback_enabled,
                        command.callback_token,
                        command.callback_aes_key,
                    )
                )
                if command.agent_id is None and not callback_only:
                    raise ProfileError("WeCom Agent ID is required")
                config = WeComProfileConfig(
                    profile_id=profile.id,
                    agent_id=command.agent_id,
                    enabled=profile.enabled,
                    callback_enabled=(
                        ProfileCapabilities.from_mapping(profile.capabilities).callback_enabled
                        if command.callback_enabled is None
                        else command.callback_enabled
                    ),
                    created_at=now,
                    updated_at=now,
                )
                session.add(config)
            else:
                if command.agent_id is not None:
                    if command.agent_id < 1:
                        raise ProfileError("WeCom Agent ID must be positive")
                    config.agent_id = command.agent_id
                if command.enabled is not None:
                    config.enabled = command.enabled
                if command.callback_enabled is not None:
                    config.callback_enabled = command.callback_enabled
                config.updated_at = now
            configured: dict[str, object] = {
                "agent_id_configured": config.agent_id is not None,
                "enabled": bool(config.enabled),
                "callback_enabled": bool(config.callback_enabled),
            }
        await self._put_secrets(
            profile_id,
            wecom_secret=command.wecom_secret,
            callback_token=command.callback_token,
            callback_aes_key=command.callback_aes_key,
        )
        if self._registry is not None:
            await self._registry.invalidate(profile_id)
        return configured

    async def set_member(self, profile_id: str, person_id: str, *, enabled: bool = True) -> None:
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            await self._get(session, profile_id)
            if await session.get(Person, person_id) is None:
                raise ProfileError("person does not exist")
            member = await session.scalar(
                select(ProfileMember).where(
                    ProfileMember.profile_id == profile_id,
                    ProfileMember.person_id == person_id,
                )
            )
            if member is None:
                session.add(
                    ProfileMember(
                        id=new_id("pm"),
                        profile_id=profile_id,
                        person_id=person_id,
                        enabled=enabled,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                member.enabled = enabled
                member.updated_at = now

    async def remove_member(self, profile_id: str, person_id: str) -> bool:
        async with self._sessions() as session, session.begin():
            await self._get(session, profile_id)
            result = await session.execute(
                delete(ProfileMember).where(
                    ProfileMember.profile_id == profile_id,
                    ProfileMember.person_id == person_id,
                )
            )
            return bool(cast(CursorResult[Any], result).rowcount)

    async def members(
        self, profile_id: str, *, enabled_only: bool = False
    ) -> list[dict[str, object]]:
        async with self._sessions() as session:
            await self._get(session, profile_id)
            statement = (
                select(ProfileMember, Person)
                .join(Person, Person.id == ProfileMember.person_id)
                .where(ProfileMember.profile_id == profile_id)
                .order_by(Person.display_name, Person.id)
            )
            if enabled_only:
                statement = statement.where(
                    ProfileMember.enabled.is_(True), Person.active.is_(True)
                )
            return [
                {
                    "person_id": member.person_id,
                    "display_name": person.display_name,
                    "enabled": bool(member.enabled),
                    "person_active": bool(person.active),
                }
                for member, person in (await session.execute(statement)).all()
            ]

    async def _put_secrets(
        self,
        profile_id: str,
        *,
        wecom_secret: str | None,
        callback_token: str | None,
        callback_aes_key: str | None,
    ) -> None:
        if self._secret_store is None:
            if any(value for value in (wecom_secret, callback_token, callback_aes_key)):
                raise ProfileError("encrypted secret storage is not configured")
            return
        for name, value in (
            ("wecom_secret", wecom_secret),
            ("wecom_callback_token", callback_token),
            ("wecom_callback_aes_key", callback_aes_key),
        ):
            if value is not None:
                if not value.strip():
                    raise ProfileError(f"{name} cannot be empty")
                await self._secret_store.put("application_profile", profile_id, name, value)

    @staticmethod
    async def _get(session: AsyncSession, profile_id: str) -> ApplicationProfile:
        profile = await session.get(ApplicationProfile, profile_id)
        if profile is None:
            raise ProfileNotFound(f"application profile {profile_id!r} was not found")
        return profile
