from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.application.ai_profile_references import referenced_ai_profiles
from app.infrastructure.database.ai_models import AIProfile, AIProvider, AIProviderModel
from app.infrastructure.database.base import new_id
from app.infrastructure.database.plugin_models import PluginConfig, PluginRecord
from app.plugin_runtime.manifest import PluginManifest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class AIResourceNotFoundError(LookupError):
    pass


class AIResourceConflictError(RuntimeError):
    pass


class AIProviderUrlError(ValueError):
    pass


class AIModelNotAllowedError(ValueError):
    pass


class AIProfileInUseError(RuntimeError):
    pass


class AIProviderInUseError(RuntimeError):
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
        raise AIProviderUrlError("base_url query parameters must use custom_query")
    if parsed.scheme != "https" and not allow_private_network:
        raise AIProviderUrlError("HTTP base_url requires private network access")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AIControlService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._factory = factory
        self._clock = clock

    async def bootstrap_if_empty(
        self,
        *,
        enabled: bool,
        preset: str,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
        secret_store: Any = None,
    ) -> bool:
        if not enabled:
            return False
        async with self._factory() as session:
            provider_count = await session.scalar(select(func.count(AIProvider.id)))
        if int(provider_count or 0) > 0:
            return False

        preset_urls = {
            "openai": "https://api.openai.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "openrouter": "https://openrouter.ai/api/v1",
            "deepseek": "https://api.deepseek.com",
            "kimi": "https://api.moonshot.cn/v1",
            "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
            "siliconflow": "https://api.siliconflow.cn/v1",
        }
        resolved_url = base_url or preset_urls.get(preset)
        if not resolved_url or not model:
            return False
        if api_key and secret_store is None:
            raise RuntimeError("AI bootstrap API key requires NOTIFY_HUB_SECRET_ENCRYPTION_KEY")

        await self.create_provider(
            {
                "id": "aip_bootstrap",
                "name": "Bootstrap AI Provider",
                "preset": preset,
                "protocol": "openai_chat_completions",
                "base_url": resolved_url,
                "enabled": True,
                "allow_private_network": False,
                "timeout_seconds": 30,
                "max_retries": 2,
                "verify_tls": True,
                "structured_output_mode": "auto",
                "custom_query": {},
            }
        )
        await self.sync_provider_models("aip_bootstrap", [model])
        await self.set_allowed_models("aip_bootstrap", [model])
        await self.create_profile(
            {
                "id": "semantic_classifier_fast",
                "name": "Fast semantic classifier",
                "description": "Low-latency structured text classification",
                "capability": "classify",
                "provider_id": "aip_bootstrap",
                "model": model,
                "temperature": 0,
                "max_output_tokens": 160,
                "response_format": "auto",
                "output_language": "auto",
                "reasoning_effort": "low",
                "verbosity": "concise",
                "include_reason": True,
                "max_reason_characters": 200,
                "system_instructions": "",
                "timeout_seconds": 20,
                "cache_ttl_seconds": 2_592_000,
                "daily_request_limit": 500,
                "daily_token_limit": None,
                "enabled": True,
            }
        )
        if api_key:
            await secret_store.put("ai_provider", "aip_bootstrap", "api_key", api_key)
        return True

    async def list_providers(self) -> list[dict[str, Any]]:
        async with self._factory() as session:
            rows = await session.scalars(
                select(AIProvider)
                .where(AIProvider.deleted_at.is_(None))
                .order_by(AIProvider.created_at)
            )
            return [self._provider_view(row) for row in rows]

    async def create_provider(self, values: Mapping[str, Any]) -> dict[str, Any]:
        now = self._clock()
        allow_private_network = bool(values["allow_private_network"])
        if not bool(values["verify_tls"]) and not allow_private_network:
            raise AIProviderUrlError("disabling TLS verification requires private network access")
        row = AIProvider(
            id=str(values.get("id") or new_id("aip")),
            name=str(values["name"]),
            preset=str(values["preset"]),
            protocol=str(values["protocol"]),
            base_url=normalize_provider_url(
                str(values["base_url"]), allow_private_network=allow_private_network
            ),
            enabled=bool(values["enabled"]),
            allow_private_network=allow_private_network,
            timeout_seconds=float(values["timeout_seconds"]),
            max_retries=int(values["max_retries"]),
            verify_tls=bool(values["verify_tls"]),
            structured_output_mode=str(values["structured_output_mode"]),
            custom_query=dict(values.get("custom_query") or {}),
            created_at=now,
            updated_at=now,
        )
        async with self._factory() as session, session.begin():
            if await session.get(AIProvider, row.id) is not None:
                raise AIResourceConflictError("AI provider id already exists")
            session.add(row)
        return self._provider_view(row)

    async def update_provider(self, provider_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            row = await session.get(AIProvider, provider_id)
            if row is None or row.deleted_at is not None:
                raise AIResourceNotFoundError(provider_id)
            next_allow_private = bool(
                values.get("allow_private_network", row.allow_private_network)
            )
            next_base_url = str(values.get("base_url", row.base_url))
            next_verify_tls = bool(values.get("verify_tls", row.verify_tls))
            if not next_verify_tls and not next_allow_private:
                raise AIProviderUrlError(
                    "disabling TLS verification requires private network access"
                )
            normalized_url = normalize_provider_url(
                next_base_url, allow_private_network=next_allow_private
            )
            for name, value in values.items():
                setattr(row, name, value)
            row.base_url = normalized_url
            row.updated_at = self._clock()
        return self._provider_view(row)

    async def delete_provider(self, provider_id: str) -> bool:
        async with self._factory() as session, session.begin():
            row = await session.get(AIProvider, provider_id)
            if row is None:
                raise AIResourceNotFoundError(provider_id)
            if row.deleted_at is not None:
                return False
            profile_ids = list(
                await session.scalars(
                    select(AIProfile.id).where(
                        AIProfile.provider_id == provider_id,
                        AIProfile.deleted_at.is_(None),
                    )
                )
            )
            if profile_ids:
                raise AIProviderInUseError(
                    f"AI provider is used by profiles: {', '.join(profile_ids)}"
                )
            row.enabled = False
            row.deleted_at = self._clock()
            row.updated_at = row.deleted_at
            return True

    async def rollback_provider_creation(self, provider_id: str) -> None:
        """Physically remove a provider whose create transaction could not store its secret."""
        async with self._factory() as session, session.begin():
            row = await session.get(AIProvider, provider_id)
            if row is not None:
                await session.delete(row)

    async def list_provider_models(self, provider_id: str) -> list[dict[str, Any]]:
        async with self._factory() as session:
            provider = await session.get(AIProvider, provider_id)
            if provider is None or provider.deleted_at is not None:
                raise AIResourceNotFoundError(provider_id)
            rows = await session.scalars(
                select(AIProviderModel)
                .where(AIProviderModel.provider_id == provider_id)
                .order_by(AIProviderModel.model_id)
            )
            return [self._provider_model_view(row) for row in rows]

    async def sync_provider_models(
        self, provider_id: str, model_ids: list[str]
    ) -> list[dict[str, Any]]:
        cleaned = sorted({model_id.strip() for model_id in model_ids if model_id.strip()})
        if len(cleaned) > 5000 or any(len(model_id) > 300 for model_id in cleaned):
            raise ValueError("provider model list is invalid")
        discovered = set(cleaned)
        now = self._clock()
        async with self._factory() as session, session.begin():
            provider = await session.get(AIProvider, provider_id)
            if provider is None or provider.deleted_at is not None:
                raise AIResourceNotFoundError(provider_id)
            rows = list(
                await session.scalars(
                    select(AIProviderModel).where(AIProviderModel.provider_id == provider_id)
                )
            )
            existing = {row.model_id: row for row in rows}
            for row in rows:
                row.available = row.model_id in discovered
                if not row.available:
                    row.enabled = False
                row.updated_at = now
            for model_id in cleaned:
                if model_id not in existing:
                    session.add(
                        AIProviderModel(
                            id=new_id("aimodel"),
                            provider_id=provider_id,
                            model_id=model_id,
                            available=True,
                            enabled=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
        return await self.list_provider_models(provider_id)

    async def set_allowed_models(
        self, provider_id: str, model_ids: list[str]
    ) -> list[dict[str, Any]]:
        cleaned = [model_id.strip() for model_id in model_ids if model_id.strip()]
        if len(cleaned) != len(set(cleaned)) or any(len(model_id) > 300 for model_id in cleaned):
            raise ValueError("allowed model list is invalid")
        allowed = set(cleaned)
        async with self._factory() as session, session.begin():
            provider = await session.get(AIProvider, provider_id)
            if provider is None or provider.deleted_at is not None:
                raise AIResourceNotFoundError(provider_id)
            rows = list(
                await session.scalars(
                    select(AIProviderModel).where(AIProviderModel.provider_id == provider_id)
                )
            )
            available = {row.model_id for row in rows if row.available}
            if not allowed <= available:
                raise AIModelNotAllowedError("only discovered, available models can be enabled")
            now = self._clock()
            for row in rows:
                row.enabled = row.available and row.model_id in allowed
                row.updated_at = now
        return await self.list_provider_models(provider_id)

    async def list_profiles(self) -> list[dict[str, Any]]:
        async with self._factory() as session:
            rows = await session.scalars(
                select(AIProfile)
                .where(AIProfile.deleted_at.is_(None))
                .order_by(AIProfile.created_at)
            )
            return [self._profile_view(row) for row in rows]

    async def create_profile(self, values: Mapping[str, Any]) -> dict[str, Any]:
        now = self._clock()
        provider_id = str(values["provider_id"])
        async with self._factory() as session, session.begin():
            provider = await session.get(AIProvider, provider_id)
            if provider is None or provider.deleted_at is not None:
                raise AIResourceNotFoundError(provider_id)
            await self._ensure_model_allowed(session, provider_id, str(values["model"]))
            profile_id = str(values.get("id") or new_id("aiprof"))
            if await session.get(AIProfile, profile_id) is not None:
                raise AIResourceConflictError("AI profile id already exists")
            row = AIProfile(
                id=profile_id,
                name=str(values["name"]),
                description=str(values.get("description") or ""),
                capability=str(values.get("capability") or "classify"),
                provider_id=provider_id,
                model=str(values["model"]),
                temperature=float(values["temperature"]),
                max_output_tokens=int(values["max_output_tokens"]),
                response_format=str(values["response_format"]),
                output_language=str(values.get("output_language") or "auto"),
                reasoning_effort=str(values.get("reasoning_effort") or "provider_default"),
                verbosity=str(values.get("verbosity") or "standard"),
                include_reason=bool(values.get("include_reason", True)),
                max_reason_characters=int(values.get("max_reason_characters", 200)),
                system_instructions=str(values.get("system_instructions") or ""),
                timeout_seconds=float(values["timeout_seconds"]),
                cache_ttl_seconds=int(values["cache_ttl_seconds"]),
                daily_request_limit=values.get("daily_request_limit"),
                daily_token_limit=values.get("daily_token_limit"),
                enabled=bool(values["enabled"]),
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        return self._profile_view(row)

    async def update_profile(self, profile_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            row = await session.get(AIProfile, profile_id)
            if row is None or row.deleted_at is not None:
                raise AIResourceNotFoundError(profile_id)
            provider_id = values.get("provider_id")
            if provider_id is not None:
                provider = await session.get(AIProvider, provider_id)
                if provider is None or provider.deleted_at is not None:
                    raise AIResourceNotFoundError(str(provider_id))
            next_provider_id = str(provider_id or row.provider_id)
            next_model = str(values.get("model", row.model))
            await self._ensure_model_allowed(session, next_provider_id, next_model)
            changed = any(getattr(row, name) != value for name, value in values.items())
            for name, value in values.items():
                setattr(row, name, value)
            if changed:
                row.revision += 1
            row.updated_at = self._clock()
        return self._profile_view(row)

    async def delete_profile(self, profile_id: str) -> None:
        async with self._factory() as session, session.begin():
            row = await session.get(AIProfile, profile_id)
            if row is None or row.deleted_at is not None:
                raise AIResourceNotFoundError(profile_id)
            configured_plugins = await session.execute(
                select(PluginRecord, PluginConfig.config)
                .outerjoin(PluginConfig, PluginRecord.id == PluginConfig.plugin_id)
                .where(PluginRecord.enabled.is_(True))
            )
            active_users = [
                plugin.id
                for plugin, config in configured_plugins
                if profile_id
                in referenced_ai_profiles(
                    PluginManifest.model_validate(plugin.manifest), config or {}
                )
            ]
            if active_users:
                raise AIProfileInUseError(
                    f"AI profile is used by enabled plugins: {', '.join(active_users)}"
                )
            row.enabled = False
            row.deleted_at = self._clock()
            row.updated_at = row.deleted_at
            row.revision += 1

    @staticmethod
    def _provider_view(row: AIProvider) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "preset": row.preset,
            "protocol": row.protocol,
            "base_url": row.base_url,
            "enabled": row.enabled,
            "allow_private_network": row.allow_private_network,
            "timeout_seconds": row.timeout_seconds,
            "max_retries": row.max_retries,
            "verify_tls": row.verify_tls,
            "structured_output_mode": row.structured_output_mode,
            "custom_query": row.custom_query,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _profile_view(row: AIProfile) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "capability": row.capability,
            "provider_id": row.provider_id,
            "model": row.model,
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

    @staticmethod
    async def _ensure_model_allowed(session: AsyncSession, provider_id: str, model_id: str) -> None:
        row = await session.scalar(
            select(AIProviderModel).where(
                AIProviderModel.provider_id == provider_id,
                AIProviderModel.model_id == model_id,
                AIProviderModel.available.is_(True),
                AIProviderModel.enabled.is_(True),
            )
        )
        if row is None:
            raise AIModelNotAllowedError(
                "AI model must be discovered and explicitly enabled for this provider"
            )

    @staticmethod
    def _provider_model_view(row: AIProviderModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "provider_id": row.provider_id,
            "model_id": row.model_id,
            "available": row.available,
            "enabled": row.enabled,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
