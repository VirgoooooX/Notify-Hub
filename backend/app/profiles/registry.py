from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.base import ChannelMessage, ChannelResult, NotificationChannel
from app.channels.wecom.adapter import OutboundMedia, WeComAdapter
from app.channels.wecom.client import WeComClient, WeComCredentials
from app.channels.wecom.crypto import WeComCrypto
from app.config import Settings
from app.domain.clock import Clock, SystemClock
from app.infrastructure.database.profile_models import ApplicationProfile, WeComProfileConfig
from app.profiles.constants import DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY
from app.profiles.errors import (
    ProfileCapabilityDisabled,
    ProfileDisabled,
    ProfileError,
    ProfileNotConfigured,
    ProfileNotFound,
    ProfileSecretMissing,
)
from app.profiles.types import DEFAULT_CAPABILITIES, ProfileCapabilities, ProfileMetadata


@dataclass(slots=True)
class WeComRuntime:
    profile: ProfileMetadata
    credentials: WeComCredentials
    client: WeComClient
    channel: WeComAdapter
    callback_crypto: WeComCrypto | None = None


MediaFactory = Callable[[str, WeComClient], OutboundMedia | None]


class ProfileRegistry:
    """Resolve persisted profiles and lazily own one WeCom runtime per profile."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        secret_store: Any = None,
        clock: Clock | None = None,
        media_factory: MediaFactory | None = None,
        http_client_factory: Callable[[WeComCredentials], Any] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._secret_store = secret_store
        self._clock = clock or SystemClock()
        self._media_factory = media_factory
        self._http_client_factory = http_client_factory
        self._runtimes: dict[str, WeComRuntime] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_default_profile(self) -> ProfileMetadata:
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            row = await session.get(ApplicationProfile, DEFAULT_PROFILE_ID)
            if row is None:
                await session.execute(
                    ApplicationProfile.__table__.update()
                    .where(ApplicationProfile.is_default.is_(True))
                    .values(is_default=False, updated_at=now)
                )
                row = ApplicationProfile(
                    id=DEFAULT_PROFILE_ID,
                    key=DEFAULT_PROFILE_KEY,
                    name="Notify Hub",
                    enabled=True,
                    is_default=True,
                    capabilities=dict(DEFAULT_CAPABILITIES),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            elif not row.is_default:
                await session.execute(
                    ApplicationProfile.__table__.update()
                    .where(ApplicationProfile.id != DEFAULT_PROFILE_ID)
                    .values(is_default=False, updated_at=now)
                )
                row.is_default = True
                row.updated_at = now
            return self._metadata(row)

    def set_media_factory(self, media_factory: MediaFactory | None) -> None:
        """Attach the shared media service after storage wiring is initialized."""

        if self._runtimes:
            raise RuntimeError("media factory must be configured before creating runtimes")
        self._media_factory = media_factory

    async def list_profiles(self, *, include_disabled: bool = True) -> list[ProfileMetadata]:
        async with self._sessions() as session:
            statement = select(ApplicationProfile).order_by(
                ApplicationProfile.is_default.desc(), ApplicationProfile.name, ApplicationProfile.id
            )
            if not include_disabled:
                statement = statement.where(ApplicationProfile.enabled.is_(True))
            rows = list(await session.scalars(statement))
            if not rows:
                return [self._default_metadata()]
            return [self._metadata(row) for row in rows]

    async def resolve(
        self, profile_ref: str | None, *, allow_disabled: bool = False
    ) -> ProfileMetadata:
        if not profile_ref:
            return await self.default_profile()
        async with self._sessions() as session:
            row = await session.scalar(
                select(ApplicationProfile).where(
                    (ApplicationProfile.id == profile_ref) | (ApplicationProfile.key == profile_ref)
                )
            )
            if row is None:
                if profile_ref in {DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY}:
                    return self._default_metadata()
                raise ProfileNotFound(f"application profile {profile_ref!r} was not found")
            return self._metadata(row) if allow_disabled else self._active_metadata(row)

    async def default_profile(self) -> ProfileMetadata:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ApplicationProfile)
                .where(ApplicationProfile.is_default.is_(True))
                .order_by(ApplicationProfile.id)
            )
            if row is not None:
                return self._active_metadata(row)
        return self._default_metadata()

    async def resolve_id(self, profile_ref: str | None) -> str:
        return (await self.resolve(profile_ref)).id

    async def runtime(self, profile_ref: str | None = None) -> WeComRuntime:
        profile = await self.resolve(profile_ref)
        lock = self._locks.setdefault(profile.id, asyncio.Lock())
        async with lock:
            cached = self._runtimes.get(profile.id)
            if cached is not None:
                return cached
            credentials, callback_crypto = await self._resolve_credentials(profile)
            client = WeComClient(credentials, self._clock)
            if self._http_client_factory is not None:
                replacement = self._http_client_factory(credentials)
                if replacement is not None:
                    client = WeComClient(credentials, self._clock, replacement)
            media = self._media_factory(profile.id, client) if self._media_factory else None
            channel = WeComAdapter(
                client,
                self._settings,
                media=media,
                agent_id=credentials.agent_id,
                broadcast_enabled=profile.capabilities.broadcast_enabled
                and self._settings.allow_broadcast,
            )
            runtime = WeComRuntime(profile, credentials, client, channel, callback_crypto)
            self._runtimes[profile.id] = runtime
            return runtime

    async def callback_crypto(self, profile_ref: str | None = None) -> WeComCrypto:
        profile = await self.resolve(profile_ref)
        _credentials, crypto = await self._resolve_credentials(profile, require_outbound=False)
        if crypto is None:
            raise ProfileNotConfigured("WeCom callback credentials are not configured")
        return crypto

    async def invalidate(self, profile_ref: str | None = None) -> None:
        if profile_ref is None:
            profile_id = (await self.default_profile()).id
        else:
            try:
                profile_id = (await self.resolve(profile_ref, allow_disabled=True)).id
            except ProfileNotFound:
                profile_id = profile_ref
        runtime = self._runtimes.pop(profile_id, None)
        if runtime is not None:
            await runtime.client.close()

    async def close(self) -> None:
        runtimes = list(self._runtimes.values())
        self._runtimes.clear()
        for runtime in runtimes:
            await runtime.client.close()

    async def _resolve_credentials(
        self, profile: ProfileMetadata, *, require_outbound: bool = True
    ) -> tuple[WeComCredentials, WeComCrypto | None]:
        if not profile.enabled:
            raise ProfileDisabled(f"application profile {profile.key!r} is disabled")
        if require_outbound and not profile.capabilities.outbound_enabled:
            raise ProfileCapabilityDisabled(
                f"outbound delivery is disabled for profile {profile.key!r}"
            )
        if not require_outbound and not profile.capabilities.callback_enabled:
            raise ProfileCapabilityDisabled(f"callbacks are disabled for profile {profile.key!r}")
        async with self._sessions() as session:
            config = await session.get(WeComProfileConfig, profile.id)

        legacy_default = profile.id == DEFAULT_PROFILE_ID
        if config is not None and not config.enabled:
            raise ProfileDisabled(f"WeCom runtime for profile {profile.key!r} is disabled")
        agent_id = (
            config.agent_id
            if config is not None
            else (self._settings.wecom_agent_id if legacy_default else None)
        )
        if require_outbound and agent_id is None:
            raise ProfileNotConfigured(f"WeCom Agent ID is not configured for {profile.key!r}")
        corp_id = self._settings.wecom_corp_id
        if not corp_id:
            raise ProfileNotConfigured("WeCom CorpID is not configured")

        secret = await self._secret("wecom_secret", profile.id)
        if not secret and legacy_default and self._settings.wecom_secret is not None:
            secret = self._settings.wecom_secret.get_secret_value()
        if require_outbound and not secret:
            raise ProfileSecretMissing(f"WeCom Secret is not configured for {profile.key!r}")

        token = await self._secret("wecom_callback_token", profile.id)
        aes_key = await self._secret("wecom_callback_aes_key", profile.id)
        if legacy_default:
            token = token or (
                self._settings.wecom_callback_token.get_secret_value()
                if self._settings.wecom_callback_token is not None
                else None
            )
            aes_key = aes_key or (
                self._settings.wecom_callback_aes_key.get_secret_value()
                if self._settings.wecom_callback_aes_key is not None
                else None
            )
        callback_crypto = None
        callback_enabled = profile.capabilities.callback_enabled and (
            config.callback_enabled if config is not None else True
        )
        if not require_outbound and not callback_enabled:
            raise ProfileCapabilityDisabled(f"callbacks are disabled for profile {profile.key!r}")
        if callback_enabled and token and aes_key:
            callback_crypto = WeComCrypto(
                token=token,
                encoding_aes_key=aes_key,
                corp_id=corp_id,
                replay_window_seconds=self._settings.wecom_callback_replay_window_seconds,
            )
        return (
            WeComCredentials(
                corp_id=corp_id,
                agent_id=agent_id,
                secret=secret,
                api_base_url=self._settings.wecom_api_base_url,
                request_timeout_seconds=self._settings.wecom_request_timeout_seconds,
                token_refresh_skew_seconds=self._settings.wecom_token_refresh_skew_seconds,
            ),
            callback_crypto,
        )

    async def _secret(self, name: str, profile_id: str) -> str | None:
        if self._secret_store is None:
            return None
        return await self._secret_store.get("application_profile", profile_id, name)

    @staticmethod
    def _metadata(row: ApplicationProfile) -> ProfileMetadata:
        return ProfileMetadata(
            id=row.id,
            key=row.key,
            name=row.name,
            enabled=bool(row.enabled),
            is_default=bool(row.is_default),
            capabilities=ProfileCapabilities.from_mapping(row.capabilities),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _active_metadata(row: ApplicationProfile) -> ProfileMetadata:
        metadata = ProfileRegistry._metadata(row)
        if not metadata.enabled:
            raise ProfileDisabled(f"application profile {metadata.key!r} is disabled")
        return metadata

    def _default_metadata(self) -> ProfileMetadata:
        now = self._clock.now()
        return ProfileMetadata(
            id=DEFAULT_PROFILE_ID,
            key=DEFAULT_PROFILE_KEY,
            name="Notify Hub",
            enabled=True,
            is_default=True,
            capabilities=ProfileCapabilities.from_mapping(DEFAULT_CAPABILITIES),
            created_at=now,
            updated_at=now,
        )


class ProfileAwareWeComChannel(NotificationChannel):
    """Shared delivery channel that selects the persisted runtime per message."""

    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry

    async def send(self, message: ChannelMessage) -> ChannelResult:
        try:
            runtime = await self._registry.runtime(message.profile_id)
        except ProfileError as exc:
            return ChannelResult(False, False, exc.code, exc.message)
        return await runtime.channel.send(message)

    async def test(self, recipient: str) -> ChannelResult:
        try:
            runtime = await self._registry.runtime()
        except ProfileError as exc:
            return ChannelResult(False, False, exc.code, exc.message)
        return await runtime.channel.test(recipient)
