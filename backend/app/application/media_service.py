from __future__ import annotations

from datetime import timedelta

from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.media_models import MediaAsset
from app.media.downloader import SafeMediaDownloader
from app.media.errors import MediaError
from app.media.storage import MediaStorage
from app.media.validation import CHANNEL_MAX_BYTES, MediaKind, validate_media
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class MediaService:
    def __init__(
        self,
        storage: MediaStorage,
        clock: Clock,
        *,
        downloader: SafeMediaDownloader | None = None,
        image_max_bytes: int = CHANNEL_MAX_BYTES,
        voice_max_bytes: int = CHANNEL_MAX_BYTES,
        voice_max_seconds: float = 60.0,
        retention_seconds: int = 24 * 60 * 60,
    ) -> None:
        if not 0 < image_max_bytes <= CHANNEL_MAX_BYTES:
            raise ValueError("image_max_bytes exceeds the channel limit")
        if not 0 < voice_max_bytes <= CHANNEL_MAX_BYTES:
            raise ValueError("voice_max_bytes exceeds the channel limit")
        if not 0 < voice_max_seconds <= 60:
            raise ValueError("voice_max_seconds exceeds the channel limit")
        self.storage = storage
        self.clock = clock
        self.downloader = downloader
        self.image_max_bytes = image_max_bytes
        self.voice_max_bytes = voice_max_bytes
        self.voice_max_seconds = voice_max_seconds
        self.retention_seconds = retention_seconds

    def limit_for(self, kind: MediaKind) -> int:
        return self.image_max_bytes if kind is MediaKind.IMAGE else self.voice_max_bytes

    async def create(
        self,
        session: AsyncSession,
        data: bytes,
        kind: MediaKind,
        *,
        source: str,
        created_by: str | None = None,
        retention_seconds: int | None = None,
    ) -> MediaAsset:
        validated = validate_media(
            data,
            kind,
            max_bytes=self.limit_for(kind),
            max_voice_seconds=self.voice_max_seconds,
        )
        stored = await self.storage.store(data, validated)
        now = self.clock.now()
        retention = self.retention_seconds if retention_seconds is None else retention_seconds
        asset = MediaAsset(
            id=new_id("media"),
            kind=kind.value,
            mime_type=validated.mime_type,
            storage_path=stored.relative_path,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            duration_seconds=validated.duration_seconds,
            source=source,
            created_by=created_by,
            created_at=now,
            expires_at=now + timedelta(seconds=retention) if retention > 0 else None,
            provider_media_id=None,
            provider_expires_at=None,
        )
        try:
            session.add(asset)
            await session.commit()
        except BaseException:
            await session.rollback()
            await self.storage.delete(stored.relative_path)
            raise
        await session.refresh(asset)
        return asset

    async def create_from_url(
        self,
        session: AsyncSession,
        url: str,
        kind: MediaKind,
        *,
        created_by: str | None = None,
    ) -> MediaAsset:
        if self.downloader is None:
            raise MediaError("media_download_disabled", "External media download is disabled")
        # The network request completes before opening the database transaction.
        data = await self.downloader.download(url, max_bytes=self.limit_for(kind))
        return await self.create(session, data, kind, source="url", created_by=created_by)

    async def get(self, session: AsyncSession, asset_id: str) -> MediaAsset:
        asset = await session.get(MediaAsset, asset_id)
        if asset is None:
            raise MediaError("media_not_found", "Media asset was not found")
        return asset

    async def list(
        self, session: AsyncSession, *, limit: int = 50, offset: int = 0
    ) -> list[MediaAsset]:
        result = await session.scalars(
            select(MediaAsset).order_by(MediaAsset.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result)

    async def read(self, asset: MediaAsset) -> bytes:
        return await self.storage.read(
            asset.storage_path, max_bytes=self.limit_for(MediaKind(asset.kind))
        )

    async def delete(self, session: AsyncSession, asset: MediaAsset) -> None:
        await self.storage.delete(asset.storage_path)
        await session.delete(asset)
        await session.commit()
