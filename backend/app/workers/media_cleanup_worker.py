from __future__ import annotations

from app.domain.clock import Clock
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.reminder_models import Reminder, ReminderOccurrence
from app.media.storage import MediaStorage
from sqlalchemy import inspect, select
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
            connection = await session.connection()
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            query = (
                select(MediaAsset)
                .where(MediaAsset.expires_at.is_not(None))
                .where(MediaAsset.expires_at <= self._clock.now())
            )
            if "reminders" in table_names:
                query = query.where(
                    ~MediaAsset.id.in_(
                        select(Reminder.media_asset_id).where(Reminder.media_asset_id.is_not(None))
                    )
                )
            if "reminder_occurrences" in table_names:
                query = query.where(
                    ~MediaAsset.id.in_(
                        select(ReminderOccurrence.media_asset_id_snapshot).where(
                            ReminderOccurrence.media_asset_id_snapshot.is_not(None)
                        )
                    )
                )
            assets = list(
                await session.scalars(query.order_by(MediaAsset.expires_at).limit(self._batch_size))
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
