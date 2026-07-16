from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.media_service import MediaService
from app.infrastructure.database.base import Base
from app.infrastructure.database.media_models import MediaAsset
from app.media.storage import MediaStorage
from app.media.validation import MediaKind
from app.workers.media_cleanup_worker import MediaCleanupWorker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class FixedClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


def amr_frame() -> bytes:
    return b"#!AMR\n" + b"\x04" + b"\x00" * 12


@pytest.mark.asyncio
async def test_expired_media_is_deleted_in_a_bounded_cleanup_batch(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'media.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = MediaStorage(tmp_path / "storage")
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    async with factory() as session:
        asset = await MediaService(storage, FixedClock(created_at), retention_seconds=1).create(
            session, amr_frame(), MediaKind.VOICE, source="test"
        )
        stored_path = storage.resolve(asset.storage_path)
        asset_id = asset.id
    assert stored_path.exists()

    worker = MediaCleanupWorker(
        factory, storage, FixedClock(created_at + timedelta(seconds=2)), batch_size=1
    )
    assert await worker.run_once() == 1
    assert not stored_path.exists()
    async with factory() as session:
        assert await session.get(MediaAsset, asset_id) is None
    await engine.dispose()
