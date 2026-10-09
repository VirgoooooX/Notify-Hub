from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.ai.provider import AIHubClient, AIProviderError
from app.infrastructure.database.ai_models import AIProfile, AIProvider
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class AIProviderUrlError(ValueError):
    pass


def normalize_provider_url(value: str, *, allow_private_network: bool) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AIProviderUrlError("base_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise AIProviderUrlError("base_url cannot contain credentials")
    if parsed.fragment:
        raise AIProviderUrlError("base_url cannot contain a fragment")
    if parsed.query:
        raise AIProviderUrlError("AI Hub URL cannot contain query parameters")
    if parsed.scheme != "https" and not allow_private_network:
        raise AIProviderUrlError("HTTP base_url requires private network access")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = path[:-7]
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _utcnow() -> datetime:
    return datetime.now(UTC)


HUB_CONNECTION_ID = "aip_family_ai_hub"


class AIControlService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = _utcnow,
        *,
        hub_client: AIHubClient | None = None,
        secret_store: Any = None,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._hub_client = hub_client or AIHubClient()
        self._secrets = secret_store
        self._refresh_lock = asyncio.Lock()

    async def ensure_connection(self) -> None:
        async with self._factory() as session, session.begin():
            if await session.get(AIProvider, HUB_CONNECTION_ID) is None:
                now = self._clock()
                session.add(
                    AIProvider(
                        id=HUB_CONNECTION_ID,
                        name="Family AI Hub",
                        preset="family_ai_hub",
                        protocol="family_ai_hub",
                        base_url="",
                        enabled=False,
                        allow_private_network=False,
                        timeout_seconds=600,
                        max_retries=0,
                        verify_tls=True,
                        structured_output_mode="auto",
                        custom_query={},
                        created_at=now,
                        updated_at=now,
                    )
                )

    async def get_connection(self) -> dict[str, Any]:
        await self.ensure_connection()
        async with self._factory() as session:
            row = await session.get(AIProvider, HUB_CONNECTION_ID)
            assert row is not None
            return {
                name: getattr(row, name)
                for name in (
                    "base_url",
                    "enabled",
                    "allow_private_network",
                    "timeout_seconds",
                    "verify_tls",
                )
            }

    async def update_connection(self, values: Mapping[str, Any]) -> dict[str, Any]:
        allow_private = bool(values["allow_private_network"])
        if not values["verify_tls"] and not allow_private:
            raise AIProviderUrlError("disabling TLS verification requires private network access")
        values = dict(values)
        values["base_url"] = normalize_provider_url(
            str(values["base_url"]), allow_private_network=allow_private
        )
        await self.ensure_connection()
        async with self._factory() as session, session.begin():
            row = await session.get(AIProvider, HUB_CONNECTION_ID)
            assert row is not None
            if any(getattr(row, name) != value for name, value in values.items()):
                await session.execute(update(AIProfile).values(revision=AIProfile.revision + 1))
            for name, value in values.items():
                setattr(row, name, value)
            row.updated_at = self._clock()
        return await self.get_connection()

    async def invalidate_caches(self) -> None:
        async with self._factory() as session, session.begin():
            await session.execute(update(AIProfile).values(revision=AIProfile.revision + 1))

    async def bootstrap_connection(
        self,
        *,
        base_url: str | None,
        application_key: str | None,
        allow_private_network: bool,
        secret_store: Any = None,
    ) -> None:
        current = await self.get_connection()
        if current["base_url"] or not base_url:
            return
        if application_key and secret_store is None:
            raise RuntimeError("AI Hub application key requires NOTIFY_HUB_SECRET_ENCRYPTION_KEY")
        values = {
            **current,
            "base_url": base_url,
            "enabled": True,
            "allow_private_network": allow_private_network,
        }
        # Validate before storing the credential.
        normalize_provider_url(base_url, allow_private_network=allow_private_network)
        if application_key:
            await secret_store.put("ai_hub", HUB_CONNECTION_ID, "application_key", application_key)
        await self.update_connection(values)

    async def export_hub_routing(self) -> dict[str, Any]:
        """Export only historical route choices; prompts and credentials stay local."""
        async with self._factory() as session:
            rows = await session.execute(
                select(AIProfile, AIProvider.protocol)
                .join(AIProvider, AIProfile.provider_id == AIProvider.id)
                .where(AIProfile.deleted_at.is_(None))
                .order_by(AIProfile.created_at)
            )
            protocols = {"openai_chat_completions": "chat", "openai_responses": "responses"}
            profiles = {
                profile.id: {
                    "protocol": protocols.get(protocol, "chat"),
                    "models": [profile.model] if profile.model else [],
                    "name": profile.name,
                    "description": profile.description,
                    "purpose": profile.capability,
                    "enabled": profile.enabled,
                    "parameters": {
                        "temperature": profile.temperature,
                        "max_output_tokens": profile.max_output_tokens,
                        "reasoning_effort": None
                        if profile.reasoning_effort == "provider_default"
                        else profile.reasoning_effort,
                        "timeout_seconds": profile.timeout_seconds,
                    },
                    **{
                        name: getattr(profile, name)
                        for name in (
                            "response_format",
                            "output_language",
                            "verbosity",
                            "include_reason",
                            "max_reason_characters",
                            "cache_ttl_seconds",
                            "daily_request_limit",
                            "daily_token_limit",
                        )
                    },
                }
                for profile, protocol in rows
            }
        return {
            "name": "Notify Hub",
            "slug": "notify-hub",
            "enabled": True,
            "default_profile": {"protocol": "chat", "models": []},
            "profiles": profiles,
            "max_attempts": 3,
            "attempt_timeout_seconds": 120,
        }

    async def refresh_profiles(self) -> None:
        """Materialize the authenticated center catalog for local logs and plugin references."""
        async with self._refresh_lock:
            await self.ensure_connection()
            async with self._factory() as session:
                connection = await session.get(AIProvider, HUB_CONNECTION_ID)
                if connection is None or not connection.enabled or not connection.base_url:
                    raise AIProviderError(
                        "ai_hub_not_configured", "AI Hub connection is not enabled"
                    )
                session.expunge(connection)
            key = (
                await self._secrets.get("ai_hub", HUB_CONNECTION_ID, "application_key")
                if self._secrets
                else None
            )
            catalog = await self._hub_client.list_profiles(connection, application_key=key)
            supported = [
                profile
                for profile in catalog
                if profile.purpose in {"classify", "extract", "summarize"}
            ]
            now = self._clock()
            async with self._factory() as session, session.begin():
                rows = {row.id: row for row in await session.scalars(select(AIProfile))}
                seen = set()
                for profile in supported:
                    seen.add(profile.id)
                    row = rows.get(profile.id)
                    if row is None:
                        row = AIProfile(
                            id=profile.id,
                            provider_id=HUB_CONNECTION_ID,
                            model="",
                            system_instructions="",
                            revision=0,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(row)
                    if row.hub_revision != profile.revision or row.deleted_at is not None:
                        values = profile.model_dump(
                            include={
                                "description",
                                "response_format",
                                "output_language",
                                "verbosity",
                                "include_reason",
                                "max_reason_characters",
                                "cache_ttl_seconds",
                                "daily_request_limit",
                                "daily_token_limit",
                            }
                        )
                        values.update(
                            name=profile.name or profile.id,
                            capability=profile.purpose,
                            temperature=profile.parameters.temperature or 0,
                            max_output_tokens=profile.parameters.max_output_tokens or 2048,
                            reasoning_effort=profile.parameters.reasoning_effort
                            or "provider_default",
                            timeout_seconds=profile.parameters.timeout_seconds or 600,
                            enabled=profile.enabled and profile.available,
                        )
                        for name, value in values.items():
                            setattr(row, name, value)
                        row.revision += 1
                        row.hub_revision, row.deleted_at, row.updated_at = (
                            profile.revision,
                            None,
                            now,
                        )
                for row in rows.values():
                    if row.id not in seen and (row.enabled or row.hub_revision is not None):
                        row.enabled = False
                        row.hub_revision = None
                        row.revision += 1
                        row.updated_at = now

    async def list_profiles(self) -> list[dict[str, Any]]:
        await self.refresh_profiles()
        async with self._factory() as session:
            rows = await session.scalars(
                select(AIProfile)
                .where(AIProfile.deleted_at.is_(None), AIProfile.hub_revision.is_not(None))
                .order_by(AIProfile.created_at)
            )
            return [self._profile_view(row) for row in rows]

    @staticmethod
    def _profile_view(row: AIProfile) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "capability": row.capability,
            "temperature": row.temperature,
            "max_output_tokens": row.max_output_tokens,
            "response_format": row.response_format,
            "output_language": row.output_language,
            "reasoning_effort": row.reasoning_effort,
            "verbosity": row.verbosity,
            "include_reason": row.include_reason,
            "max_reason_characters": row.max_reason_characters,
            "system_instructions": row.system_instructions,
            "timeout_seconds": row.timeout_seconds,
            "cache_ttl_seconds": row.cache_ttl_seconds,
            "daily_request_limit": row.daily_request_limit,
            "daily_token_limit": row.daily_token_limit,
            "enabled": row.enabled,
            "revision": row.revision,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
