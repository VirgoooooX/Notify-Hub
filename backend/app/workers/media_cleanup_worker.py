from __future__ import annotations

from app.domain.clock import Clock
from app.infrastructure.database.media_models import MediaAsset
from app.media.storage import MediaStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class MediaCleanupWorker:
    """Deletes expired media in bounded batches; failures remain retryable."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: MediaStorage,
        clock: Clock,
        *,
        batch_size: int = 100,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._clock = clock
        self._batch_size = batch_size

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            assets = list(
                await session.scalars(
                    select(MediaAsset)
                    .where(MediaAsset.expires_at.is_not(None))
                    .where(MediaAsset.expires_at <= self._clock.now())
                    .order_by(MediaAsset.expires_at)
                    .limit(self._batch_size)
                )
            )
            deleted = 0
            for asset in assets:
                try:
                    await self._storage.delete(asset.storage_path)
                except OSError:
                    continue
                await session.delete(asset)
                deleted += 1
            await session.commit()
            return deleted
