from __future__ import annotations

from datetime import datetime

from app.channels.wecom.media_adapter import WeComTemporaryMediaAdapter
from app.infrastructure.database.media_models import MediaAsset
from app.media.storage import MediaStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DatabaseMediaCacheRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save_provider_cache(self, asset_id: str, media_id: str, expires_at: datetime) -> None:
        async with self._sessions() as session, session.begin():
            asset = await session.get(MediaAsset, asset_id)
            if asset is not None:
                asset.provider_media_id = media_id
                asset.provider_expires_at = expires_at


class OutboundWeComMediaService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: MediaStorage,
        adapter: WeComTemporaryMediaAdapter,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._adapter = adapter

    async def media_id(self, asset_id: str, *, now: datetime) -> str:
        async with self._sessions() as session:
            asset = await session.get(MediaAsset, asset_id)
            if asset is None:
                raise ValueError("Media asset does not exist")
            content = await self._storage.read(asset.storage_path, max_bytes=2_097_152)
            uploaded = await self._adapter.ensure_uploaded(asset, content, now=now)
            return uploaded.media_id
