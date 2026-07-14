from __future__ import annotations

import struct
import zlib
import time
from typing import Any

import httpx
import pytest
from app.media.validation import MediaKind
from app.infrastructure.security.tokens import generate_media_signature
from app.infrastructure.database.media_models import MediaAsset
from sqlalchemy import update


def png_bytes() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


@pytest.mark.asyncio
async def test_public_media_retrieval_success_and_errors(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = api
    media_service = app.state.media_service
    session_factory = app.state.session_factory
    settings = app.state.settings

    # 1. Create a valid image asset
    async with session_factory() as session:
        image_asset = await media_service.create(
            session,
            png_bytes(),
            MediaKind.IMAGE,
            source="test",
            created_by="test-user",
            retention_seconds=3600,
        )
        image_id = image_asset.id

    # 2. Retrieve with a valid signature
    expires = int(time.time()) + 1800
    key = settings.public_media_signing_key.get_secret_value()
    sig = generate_media_signature(image_id, expires, key)

    response = await client.get(f"/public/media/{image_id}?expires={expires}&sig={sig}")
    assert response.status_code == 200
    assert response.content == png_bytes()
    assert response.headers["Content-Type"] == "image/png"
    assert "public, max-age=86400" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["ETag"] == image_asset.checksum_sha256

    # 3. Request with an invalid signature
    bad_response = await client.get(f"/public/media/{image_id}?expires={expires}&sig=badsignature")
    assert bad_response.status_code == 403
    assert "Invalid signature" in bad_response.text

    # 4. Request with an expired signature (URL parameter expired)
    past_expires = int(time.time()) - 10
    expired_sig = generate_media_signature(image_id, past_expires, key)
    expired_response = await client.get(f"/public/media/{image_id}?expires={past_expires}&sig={expired_sig}")
    assert expired_response.status_code == 403
    assert "Signature has expired" in expired_response.text

    # 5. Request with a non-existent asset ID
    fake_id = "media_99999999999999999999999999999999"
    fake_sig = generate_media_signature(fake_id, expires, key)
    missing_response = await client.get(f"/public/media/{fake_id}?expires={expires}&sig={fake_sig}")
    assert missing_response.status_code == 404

    # 6. Request with invalid ID format (contains dot, fails ASSET_ID_RE check)
    traversal_id = "invalid-id-with-dot.png"
    # Even if signature was generated for it
    traversal_sig = generate_media_signature(traversal_id, expires, key)
    traversal_response = await client.get(f"/public/media/{traversal_id}?expires={expires}&sig={traversal_sig}")
    assert traversal_response.status_code == 422


@pytest.mark.asyncio
async def test_public_media_types_and_lifecycle(
    api: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = api
    media_service = app.state.media_service
    session_factory = app.state.session_factory
    settings = app.state.settings

    # 1. Create a voice asset
    voice_data = b"#!AMR\n" + (b"\x04" + b"\x00" * 12) * 10
    async with session_factory() as session:
        voice_asset = await media_service.create(
            session,
            voice_data,
            MediaKind.VOICE,
            source="test",
            created_by="test-user",
            retention_seconds=3600,
        )
        voice_id = voice_asset.id

    expires = int(time.time()) + 1800
    key = settings.public_media_signing_key.get_secret_value()

    # Attempting to read voice asset should return 403
    voice_sig = generate_media_signature(voice_id, expires, key)
    voice_response = await client.get(f"/public/media/{voice_id}?expires={expires}&sig={voice_sig}")
    assert voice_response.status_code == 403
    assert "Only image assets are publicly accessible" in voice_response.text

    # 2. Create an image asset but set its expires_at in the database to the past
    async with session_factory() as session:
        expired_image_asset = await media_service.create(
            session,
            png_bytes(),
            MediaKind.IMAGE,
            source="test",
            created_by="test-user",
            retention_seconds=3600,
        )
        expired_image_id = expired_image_asset.id
        from datetime import timedelta
        await session.execute(
            update(MediaAsset)
            .where(MediaAsset.id == expired_image_id)
            .values(expires_at=app.state.clock.now() - timedelta(seconds=10))
        )
        await session.commit()

    expired_image_sig = generate_media_signature(expired_image_id, expires, key)
    expired_image_response = await client.get(
        f"/public/media/{expired_image_id}?expires={expires}&sig={expired_image_sig}"
    )
    assert expired_image_response.status_code == 403
    assert "Media asset has expired" in expired_image_response.text
