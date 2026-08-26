from __future__ import annotations

import io
import secrets
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.application.media_service import MediaService
from app.config import Settings
from app.infrastructure.database import Base
from app.infrastructure.database.media_models import MediaAsset
from app.media.processing import make_blurred_background_cover
from app.media.public_urls import PublicMediaUrlBuilder
from app.media.storage import MediaStorage
from app.media.validation import MediaKind, validate_media
from app.plugin_runtime.context import PluginMediaPublisher
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

CHANNEL_IMAGE_LIMIT = 2 * 1024 * 1024
SOURCE_IMAGE_LIMIT = 16 * 1024 * 1024


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


def oversized_jpeg() -> bytes:
    width, height = 2_000, 1_600
    pixels = secrets.token_bytes(width * height * 3)
    image = Image.frombytes("RGB", (width, height), pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, subsampling=0)
    result = buffer.getvalue()
    assert len(result) > CHANNEL_IMAGE_LIMIT
    return result


def test_oversized_source_image_is_compressed_to_channel_limit() -> None:
    source = oversized_jpeg()

    compressed = make_blurred_background_cover(source, max_bytes=CHANNEL_IMAGE_LIMIT)

    assert len(compressed) <= CHANNEL_IMAGE_LIMIT
    validated = validate_media(compressed, MediaKind.IMAGE, max_bytes=CHANNEL_IMAGE_LIMIT)
    assert validated.mime_type == "image/jpeg"


def test_settings_have_a_bounded_larger_source_image_limit() -> None:
    settings = Settings(_env_file=None)

    assert settings.media_image_max_bytes == CHANNEL_IMAGE_LIMIT
    assert settings.media_source_image_max_bytes == SOURCE_IMAGE_LIMIT


@pytest.mark.asyncio
async def test_plugin_media_publisher_downloads_large_source_then_persists_compressed_image(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'media.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source = oversized_jpeg()
    downloader = SimpleNamespace(download=AsyncMock(return_value=source))
    media_service = MediaService(
        MediaStorage(tmp_path / "media"),
        FixedClock(),
        downloader=downloader,
        source_image_max_bytes=SOURCE_IMAGE_LIMIT,
        retention_seconds=3_600,
    )
    publisher = PluginMediaPublisher(
        plugin_id="fabrizio_hwg_monitor",
        media_write_allowed=True,
        media_service=media_service,
        session_factory=factory,
        public_media_urls=PublicMediaUrlBuilder("https://notify.example", "test-signing-key"),
    )

    url = await publisher.publish_image_url("https://pbs.twimg.com/media/large.jpg")

    assert "/public/media/" in url
    downloader.download.assert_awaited_once_with(
        "https://pbs.twimg.com/media/large.jpg",
        max_bytes=SOURCE_IMAGE_LIMIT,
    )
    async with factory() as session:
        asset = await session.scalar(
            select(MediaAsset).where(MediaAsset.created_by == "plugin:fabrizio_hwg_monitor")
        )
    assert asset is not None
    assert asset.size_bytes <= CHANNEL_IMAGE_LIMIT
    assert (tmp_path / "media" / asset.storage_path).is_file()
    await engine.dispose()
