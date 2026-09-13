from __future__ import annotations

from datetime import datetime

from app.channels.wecom.media_adapter import UploadedTemporaryMedia, WeComTemporaryMediaAdapter
from app.infrastructure.database.base import new_id
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.profile_models import (
    ApplicationProfile,
    MediaProviderRef,
)
from app.media.storage import MediaStorage
from app.profiles.constants import DEFAULT_PROFILE_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DatabaseMediaCacheRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        profile_id: str = DEFAULT_PROFILE_ID,
        channel: str = "wecom",
    ) -> None:
        self._sessions = sessions
        self._profile_id = profile_id
        self._channel = channel

    async def get_provider_cache(self, asset_id: str) -> UploadedTemporaryMedia | None:
        async with self._sessions() as session:
            reference = await session.scalar(
                select(MediaProviderRef).where(
                    MediaProviderRef.asset_id == asset_id,
                    MediaProviderRef.profile_id == self._profile_id,
                    MediaProviderRef.channel == self._channel,
                )
            )
            if reference is not None:
                return UploadedTemporaryMedia(reference.provider_media_id, reference.expires_at)
            if self._profile_id != DEFAULT_PROFILE_ID:
                return None
            asset = await session.get(MediaAsset, asset_id)
            if asset is None or not asset.provider_media_id or asset.provider_expires_at is None:
                return None
            return UploadedTemporaryMedia(asset.provider_media_id, asset.provider_expires_at)

    async def save_provider_cache(self, asset_id: str, media_id: str, expires_at: datetime) -> None:
        async with self._sessions() as session, session.begin():
            profile_exists = await session.scalar(
                select(ApplicationProfile.id).where(ApplicationProfile.id == self._profile_id)
            )
            if profile_exists is not None:
                reference = await session.scalar(
                    select(MediaProviderRef).where(
                        MediaProviderRef.asset_id == asset_id,
                        MediaProviderRef.profile_id == self._profile_id,
                        MediaProviderRef.channel == self._channel,
                    )
                )
                if reference is None:
                    session.add(
                        MediaProviderRef(
                            id=new_id("mref"),
                            asset_id=asset_id,
                            profile_id=self._profile_id,
                            channel=self._channel,
                            provider_media_id=media_id,
                            expires_at=expires_at,
                        )
                    )
                else:
                    reference.provider_media_id = media_id
                    reference.expires_at = expires_at
            asset = await session.get(MediaAsset, asset_id)
            if asset is not None and self._profile_id == DEFAULT_PROFILE_ID:
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
