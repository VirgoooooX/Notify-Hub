from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.channels.wecom.media_adapter import WeComTemporaryMediaAdapter
from app.infrastructure.database.media_models import MediaAsset


class Transport:
    def __init__(self) -> None:
        self.uploads = 0

    async def upload_temporary_media(
        self, *, media_type: str, filename: str, mime_type: str, content: bytes
    ) -> str:
        assert media_type == "voice"
        assert filename.endswith(".amr")
        assert mime_type == "audio/amr"
        assert content.startswith(b"#!AMR\n")
        self.uploads += 1
        return f"provider-{self.uploads}"

    async def download_temporary_media(self, media_id: str, *, max_bytes: int) -> bytes:
        assert media_id
        assert max_bytes > 0
        return b"#!AMR\n" + b"\x04" + b"\x00" * 12


class Cache:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, datetime]] = []

    async def save_provider_cache(self, asset_id: str, media_id: str, expires_at: datetime) -> None:
        self.saved.append((asset_id, media_id, expires_at))


def voice_asset(now: datetime) -> MediaAsset:
    return MediaAsset(
        id="media-1",
        kind="voice",
        mime_type="audio/amr",
        storage_path="voice/aa/random.amr",
        checksum_sha256="0" * 64,
        size_bytes=19,
        duration_seconds=0.02,
        source="upload",
        created_by=None,
        created_at=now,
        expires_at=None,
        provider_media_id="cached",
        provider_expires_at=now + timedelta(seconds=60),
    )


@pytest.mark.asyncio
async def test_expired_provider_media_is_reuploaded() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    transport = Transport()
    cache = Cache()
    adapter = WeComTemporaryMediaAdapter(transport, cache, expiry_skew_seconds=120)
    asset = voice_asset(now)

    uploaded = await adapter.ensure_uploaded(asset, b"#!AMR\n" + b"\x04" + b"\x00" * 12, now=now)
    assert uploaded.media_id == "provider-1"
    assert transport.uploads == 1
    assert cache.saved[0][0:2] == ("media-1", "provider-1")


@pytest.mark.asyncio
async def test_fresh_provider_media_is_reused_and_download_is_validated() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    transport = Transport()
    cache = Cache()
    adapter = WeComTemporaryMediaAdapter(transport, cache, expiry_skew_seconds=30)
    asset = voice_asset(now)
    asset.provider_expires_at = (now + timedelta(seconds=60)).replace(tzinfo=None)

    uploaded = await adapter.ensure_uploaded(asset, b"unused", now=now)
    assert uploaded.media_id == "cached"
    assert transport.uploads == 0
    assert await adapter.download_voice("incoming") == (b"#!AMR\n" + b"\x04" + b"\x00" * 12)
