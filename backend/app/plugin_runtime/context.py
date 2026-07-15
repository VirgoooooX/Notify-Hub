from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from app.ai.schemas import (
    AIClassificationItem,
    AIClassificationResult,
    AIExtractionResult,
    AISummaryResult,
)
from app.plugin_runtime.base import EventDraft, EventReceipt
from app.plugin_runtime.http import RestrictedHttpClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.application.media_service import MediaService


@dataclass(frozen=True)
class StateValue:
    value: Any
    version: int


class StateStore(Protocol):
    async def get(self, plugin_id: str, key: str) -> StateValue | None: ...
    async def set(
        self, plugin_id: str, key: str, value: Any, expected_version: int | None
    ) -> int: ...
    async def save_checkpoint(self, plugin_id: str, values: Mapping[str, Any]) -> None: ...


class ConfigStore(Protocol):
    async def get(self, plugin_id: str) -> dict[str, Any]: ...


class EventEmitter(Protocol):
    async def emit(self, plugin_id: str, event: EventDraft) -> EventReceipt: ...


class SecretResolver(Protocol):
    async def resolve(self, plugin_id: str, name: str) -> str: ...


class AIServiceProtocol(Protocol):
    async def classify(
        self,
        *,
        profile: str,
        plugin_id: str | None,
        plugin_run_id: str | None,
        use_case: str,
        content: str,
        instruction: str,
        labels: Sequence[str],
        cache_key: str | None = None,
    ) -> AIClassificationResult: ...

    async def classify_many(
        self,
        *,
        profile: str,
        plugin_id: str | None,
        plugin_run_id: str | None,
        use_case: str,
        instruction: str,
        labels: Sequence[str],
        items: Sequence[AIClassificationItem],
    ) -> list[AIClassificationResult]: ...

    async def extract(
        self,
        *,
        profile: str,
        plugin_id: str | None,
        plugin_run_id: str | None,
        use_case: str,
        content: str,
        instruction: str,
        fields: Sequence[str],
        cache_key: str | None = None,
    ) -> AIExtractionResult: ...

    async def summarize(
        self,
        *,
        profile: str,
        plugin_id: str | None,
        plugin_run_id: str | None,
        use_case: str,
        content: str,
        instruction: str,
        max_characters: int = 2000,
        cache_key: str | None = None,
    ) -> AISummaryResult: ...


class PluginAIClient:
    def __init__(
        self,
        *,
        plugin_id: str,
        run_id: str,
        allowed_profiles: set[str],
        service: AIServiceProtocol | None,
    ) -> None:
        self._plugin_id = plugin_id
        self._run_id = run_id
        self._allowed_profiles = allowed_profiles
        self._service = service

    def _authorize(self, profile: str) -> AIServiceProtocol:
        if profile not in self._allowed_profiles:
            raise PermissionError("plugin is not permitted to use this AI profile")
        if self._service is None:
            raise RuntimeError("AI service is not available")
        return self._service

    async def classify(
        self,
        *,
        profile: str,
        use_case: str,
        content: str,
        instruction: str,
        labels: Sequence[str],
        cache_key: str | None = None,
    ) -> AIClassificationResult:
        service = self._authorize(profile)
        return await service.classify(
            profile=profile,
            plugin_id=self._plugin_id,
            plugin_run_id=self._run_id,
            use_case=use_case,
            content=content,
            instruction=instruction,
            labels=labels,
            cache_key=cache_key,
        )

    async def classify_many(
        self,
        *,
        profile: str,
        use_case: str,
        instruction: str,
        labels: Sequence[str],
        items: Sequence[AIClassificationItem],
    ) -> list[AIClassificationResult]:
        service = self._authorize(profile)
        return await service.classify_many(
            profile=profile,
            plugin_id=self._plugin_id,
            plugin_run_id=self._run_id,
            use_case=use_case,
            instruction=instruction,
            labels=labels,
            items=items,
        )

    async def extract(
        self,
        *,
        profile: str,
        use_case: str,
        content: str,
        instruction: str,
        fields: Sequence[str],
        cache_key: str | None = None,
    ) -> AIExtractionResult:
        service = self._authorize(profile)
        return await service.extract(
            profile=profile,
            plugin_id=self._plugin_id,
            plugin_run_id=self._run_id,
            use_case=use_case,
            content=content,
            instruction=instruction,
            fields=fields,
            cache_key=cache_key,
        )

    async def summarize(
        self,
        *,
        profile: str,
        use_case: str,
        content: str,
        instruction: str,
        max_characters: int = 2000,
        cache_key: str | None = None,
    ) -> AISummaryResult:
        service = self._authorize(profile)
        return await service.summarize(
            profile=profile,
            plugin_id=self._plugin_id,
            plugin_run_id=self._run_id,
            use_case=use_case,
            content=content,
            instruction=instruction,
            max_characters=max_characters,
            cache_key=cache_key,
        )


class PluginContext:
    """The complete platform surface visible to a trusted v1 plugin."""

    __slots__ = (
        "_config",
        "_emitter",
        "_secrets",
        "_state",
        "ai",
        "http",
        "logger",
        "media",
        "plugin_id",
        "run_id",
    )

    def __init__(
        self,
        *,
        plugin_id: str,
        run_id: str,
        state: StateStore,
        config: ConfigStore,
        emitter: EventEmitter,
        secrets: SecretResolver,
        http: RestrictedHttpClient,
        ai: PluginAIClient,
        media: PluginMediaPublisher | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.run_id = run_id
        self._state = state
        self._config = config
        self._emitter = emitter
        self._secrets = secrets
        self.http = http
        self.media = media
        self.ai = ai
        self.logger = structlog.get_logger().bind(plugin_id=plugin_id, plugin_run_id=run_id)

    async def emit_event(self, event: Any) -> EventReceipt:
        """Normalize structural plugin models at the host trust boundary."""
        if isinstance(event, EventDraft):
            normalized = event
        else:
            raw = event.model_dump() if hasattr(event, "model_dump") else event
            if isinstance(raw, dict) and raw.get("article") is not None:
                raw = {**raw, "message_type": "article"}
            normalized = EventDraft.model_validate(raw, from_attributes=not isinstance(raw, dict))
            if normalized.article is not None and normalized.message_type == "text":
                normalized = normalized.model_copy(update={"message_type": "article"})
        return await self._emitter.emit(self.plugin_id, normalized)

    async def get_state(self, key: str, default: Any = None) -> Any:
        result = await self._state.get(self.plugin_id, key)
        return default if result is None else result.value

    async def get_state_versioned(self, key: str) -> StateValue | None:
        return await self._state.get(self.plugin_id, key)

    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int:
        return await self._state.set(self.plugin_id, key, value, expected_version)

    async def get_secret(self, name: str) -> str:
        return await self._secrets.resolve(self.plugin_id, name)

    async def get_config(self) -> dict[str, Any]:
        return await self._config.get(self.plugin_id)

    async def save_checkpoint(self, values: Mapping[str, Any]) -> None:
        await self._state.save_checkpoint(self.plugin_id, values)


class PluginMediaPublisher:
    def __init__(
        self,
        *,
        plugin_id: str,
        media_write_allowed: bool,
        media_service: MediaService | None,
        session_factory: async_sessionmaker[AsyncSession] | None,
        public_base_url: str | None,
        signing_key: bytes,
    ) -> None:
        self._plugin_id = plugin_id
        self._media_write_allowed = media_write_allowed
        self._media_service = media_service
        self._factory = session_factory
        self._public_base_url = public_base_url
        self._signing_key = signing_key

    async def publish_image_url(
        self,
        source_url: str,
        *,
        retention_seconds: int | None = None,
    ) -> str:
        if not self._media_write_allowed:
            raise PermissionError("plugin does not have media_write permission")
        if self._media_service is None or self._factory is None:
            raise RuntimeError("media service is not available")

        downloader = self._media_service.downloader
        if downloader is None:
            from app.media.errors import MediaError

            raise MediaError("media_download_disabled", "External media download is disabled")

        from app.media.validation import MediaKind

        data = await downloader.download(
            source_url, max_bytes=self._media_service.limit_for(MediaKind.IMAGE)
        )

        from app.media.processing import make_blurred_background_cover

        data = make_blurred_background_cover(data)

        duration = (
            retention_seconds
            if retention_seconds is not None
            else self._media_service.retention_seconds
        )

        async with self._factory() as session:
            asset = await self._media_service.create(
                session,
                data,
                MediaKind.IMAGE,
                source="url",
                created_by=f"plugin:{self._plugin_id}",
                retention_seconds=duration,
            )

        import time

        expires = int(time.time()) + duration

        from app.infrastructure.security.tokens import generate_media_signature

        sig = generate_media_signature(asset.id, expires, self._signing_key.decode("utf-8"))
        base_url = self._public_base_url or "http://localhost:8000"
        return f"{base_url.rstrip('/')}/public/media/{asset.id}?expires={expires}&sig={sig}"
