from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.infrastructure.database.media_models import MediaAsset
from app.media.errors import MediaError, MediaTooLargeError
from app.media.validation import CHANNEL_MAX_BYTES, MediaKind, validate_media


@dataclass(frozen=True, slots=True)
class UploadedTemporaryMedia:
    media_id: str
    expires_at: datetime


class WeComMediaTransport(Protocol):
    async def upload_temporary_media(
        self, *, media_type: str, filename: str, mime_type: str, content: bytes
    ) -> str: ...

    async def download_temporary_media(self, media_id: str, *, max_bytes: int) -> bytes: ...


class MediaCacheRepository(Protocol):
    async def save_provider_cache(
        self, asset_id: str, media_id: str, expires_at: datetime
    ) -> None: ...


class WeComTemporaryMediaAdapter:
    """Adds expiry-aware cache semantics to the provider media transport."""

    def __init__(
        self,
        transport: WeComMediaTransport,
        cache: MediaCacheRepository,
        *,
        temporary_ttl_seconds: int = 3 * 24 * 60 * 60,
        expiry_skew_seconds: int = 120,
    ) -> None:
        self._transport = transport
        self._cache = cache
        self._ttl = temporary_ttl_seconds
        self._skew = expiry_skew_seconds

    async def ensure_uploaded(
        self, asset: MediaAsset, content: bytes, *, now: datetime
    ) -> UploadedTemporaryMedia:
        if len(content) > CHANNEL_MAX_BYTES:
            raise MediaTooLargeError(CHANNEL_MAX_BYTES)
        provider_expires_at = asset.provider_expires_at
        if provider_expires_at is not None and provider_expires_at.tzinfo is None:
            provider_expires_at = provider_expires_at.replace(tzinfo=UTC)
        if (
            asset.provider_media_id
            and provider_expires_at
            and provider_expires_at > now + timedelta(seconds=self._skew)
        ):
            return UploadedTemporaryMedia(asset.provider_media_id, provider_expires_at)

        media_type = "image" if asset.kind == "image" else "voice"
        extension = ".jpg" if asset.mime_type == "image/jpeg" else ".png"
        if media_type == "voice":
            extension = ".amr"
        media_id = await self._transport.upload_temporary_media(
            media_type=media_type,
            filename=f"media{extension}",
            mime_type=asset.mime_type,
            content=content,
        )
        if not media_id:
            raise MediaError("wecom_media_upload_failed", "Provider returned no media identifier")
        expires_at = now + timedelta(seconds=self._ttl)
        await self._cache.save_provider_cache(asset.id, media_id, expires_at)
        return UploadedTemporaryMedia(media_id, expires_at)

    async def download_voice(
        self,
        media_id: str,
        *,
        max_bytes: int = CHANNEL_MAX_BYTES,
        max_seconds: float = 60.0,
    ) -> bytes:
        if not media_id:
            raise MediaError("invalid_media_id", "Provider media identifier is empty")
        if not 0 < max_bytes <= CHANNEL_MAX_BYTES:
            raise ValueError("max_bytes exceeds the channel limit")
        content = await self._transport.download_temporary_media(media_id, max_bytes=max_bytes)
        validate_media(content, MediaKind.VOICE, max_bytes=max_bytes, max_voice_seconds=max_seconds)
        return content
