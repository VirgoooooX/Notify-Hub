from __future__ import annotations

import ipaddress
import struct
import zlib
from pathlib import Path

import httpx
import pytest
from app.media.downloader import SafeMediaDownloader
from app.media.errors import MediaError, MediaTooLargeError, UnsafeMediaUrlError
from app.media.storage import MediaStorage
from app.media.validation import MediaKind, validate_media


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


def test_real_mime_rejects_renamed_and_appended_content() -> None:
    with pytest.raises(MediaError, match="JPG and PNG"):
        validate_media(b"not an image", MediaKind.IMAGE)
    with pytest.raises(MediaError, match="JPG and PNG"):
        validate_media(png_bytes() + b"<script>", MediaKind.IMAGE)


def test_size_and_amr_duration_limits_are_hard_caps() -> None:
    with pytest.raises(MediaTooLargeError):
        validate_media(b"x" * 11, MediaKind.IMAGE, max_bytes=10)
    # AMR mode 0 frames are 13 bytes including frame header; 3001 frames exceed 60 s.
    voice = b"#!AMR\n" + (b"\x04" + b"\x00" * 12) * 3001
    with pytest.raises(MediaError, match="60 second"):
        validate_media(voice, MediaKind.VOICE)
    with pytest.raises(ValueError, match="channel limit"):
        validate_media(png_bytes(), MediaKind.IMAGE, max_bytes=2 * 1024 * 1024 + 1)


@pytest.mark.asyncio
async def test_storage_randomizes_path_and_rejects_traversal(tmp_path: Path) -> None:
    storage = MediaStorage(tmp_path)
    media = validate_media(png_bytes(), MediaKind.IMAGE)
    first = await storage.store(png_bytes(), media)
    second = await storage.store(png_bytes(), media)
    assert first.relative_path != second.relative_path
    assert first.checksum_sha256 == second.checksum_sha256
    assert await storage.read(first.relative_path, max_bytes=2 * 1024 * 1024) == png_bytes()
    with pytest.raises(MediaError, match="controlled storage"):
        storage.resolve("../secret")


@pytest.mark.asyncio
async def test_downloader_blocks_private_and_redirect_destinations() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async def resolver(host: str, _port: int) -> set[ipaddress.IPv4Address]:
        if host == "public.example":
            return {ipaddress.ip_address("93.184.216.34")}
        return {ipaddress.ip_address("127.0.0.1")}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = SafeMediaDownloader(client, resolver=resolver)
        with pytest.raises(UnsafeMediaUrlError):
            await downloader.download("http://127.0.0.1/a", max_bytes=100)
        assert requests == 0
        with pytest.raises(UnsafeMediaUrlError):
            await downloader.download("https://public.example/a", max_bytes=100)
        assert requests == 1


@pytest.mark.asyncio
async def test_downloader_enforces_stream_limit_and_timeout() -> None:
    async def public_resolver(_host: str, _port: int) -> set[ipaddress.IPv4Address]:
        return {ipaddress.ip_address("93.184.216.34")}

    async def too_large(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 11)

    async with httpx.AsyncClient(transport=httpx.MockTransport(too_large)) as client:
        with pytest.raises(MediaTooLargeError):
            await SafeMediaDownloader(client, resolver=public_resolver).download(
                "https://example.test/a", max_bytes=10
            )

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(MediaError, match="timed out"):
            await SafeMediaDownloader(client, resolver=public_resolver).download(
                "https://example.test/a", max_bytes=10
            )
