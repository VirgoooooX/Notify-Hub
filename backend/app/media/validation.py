from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass

from app.media.errors import MediaError, MediaTooLargeError

CHANNEL_MAX_BYTES = 2 * 1024 * 1024
CHANNEL_MAX_VOICE_SECONDS = 60.0


class MediaKind(str, enum.Enum):
    IMAGE = "image"
    VOICE = "voice"


@dataclass(frozen=True, slots=True)
class ValidatedMedia:
    kind: MediaKind
    mime_type: str
    extension: str
    size_bytes: int
    duration_seconds: float | None = None


def _validate_png(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    position = 8
    seen_ihdr = False
    seen_iend = False
    while position + 12 <= len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        if length > len(data) - position - 12:
            return False
        chunk_type = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        expected_crc = struct.unpack(">I", data[position + 8 + length : position + 12 + length])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            return False
        if not seen_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width, height = struct.unpack(">II", payload[:8])
            if width == 0 or height == 0:
                return False
            seen_ihdr = True
        if chunk_type == b"IEND":
            if length != 0:
                return False
            seen_iend = True
            position += 12
            break
        position += 12 + length
    return seen_ihdr and seen_iend and position == len(data)


def _validate_jpeg(data: bytes) -> bool:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return False
    # Requiring a valid SOF marker prevents accepting a renamed arbitrary blob.
    position = 2
    seen_sof = False
    while position < len(data) - 2:
        if data[position] != 0xFF:
            # Entropy-coded scan data is allowed only after SOS.
            return seen_sof
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            return False
        marker = data[position]
        position += 1
        if marker == 0xD9:
            return seen_sof and position == len(data)
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if position + 2 > len(data):
            return False
        segment_length = struct.unpack(">H", data[position : position + 2])[0]
        if segment_length < 2 or position + segment_length > len(data):
            return False
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_length < 8:
                return False
            seen_sof = True
        if marker == 0xDA:
            # Validate framing through the final EOI. Stuffed FF00 and restart
            # markers are legal; any other marker ends the scan.
            scan = position + segment_length
            while scan < len(data) - 1:
                if data[scan] != 0xFF:
                    scan += 1
                    continue
                following = data[scan + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    scan += 2
                    continue
                if following == 0xD9:
                    return seen_sof and scan + 2 == len(data)
                position = scan
                break
            else:
                return False
            continue
        position += segment_length
    return False


_AMR_FRAME_BYTES = (13, 14, 16, 18, 20, 21, 27, 32, 6)


def _amr_duration(data: bytes) -> float | None:
    if not data.startswith(b"#!AMR\n"):
        return None
    position = 6
    frames = 0
    while position < len(data):
        frame_type = (data[position] >> 3) & 0x0F
        if frame_type >= len(_AMR_FRAME_BYTES):
            return None
        frame_size = _AMR_FRAME_BYTES[frame_type]
        if position + frame_size > len(data):
            return None
        position += frame_size
        frames += 1
    return frames * 0.02


def validate_media(
    data: bytes,
    kind: MediaKind,
    *,
    max_bytes: int = CHANNEL_MAX_BYTES,
    max_voice_seconds: float = CHANNEL_MAX_VOICE_SECONDS,
) -> ValidatedMedia:
    if max_bytes <= 0 or max_bytes > CHANNEL_MAX_BYTES:
        raise ValueError("max_bytes must not exceed the channel limit")
    if len(data) > max_bytes:
        raise MediaTooLargeError(max_bytes)
    if not data:
        raise MediaError("empty_media", "Media is empty")

    if kind is MediaKind.IMAGE:
        if _validate_png(data):
            return ValidatedMedia(kind, "image/png", ".png", len(data))
        if _validate_jpeg(data):
            return ValidatedMedia(kind, "image/jpeg", ".jpg", len(data))
        raise MediaError("unsupported_media_type", "Only valid JPG and PNG images are accepted")

    if max_voice_seconds <= 0 or max_voice_seconds > CHANNEL_MAX_VOICE_SECONDS:
        raise ValueError("max_voice_seconds must not exceed the channel limit")
    duration = _amr_duration(data)
    if duration is None:
        raise MediaError("unsupported_media_type", "Only valid narrow-band AMR voice is accepted")
    if duration > max_voice_seconds:
        raise MediaError("voice_too_long", f"Voice exceeds the {max_voice_seconds:g} second limit")
    return ValidatedMedia(kind, "audio/amr", ".amr", len(data), duration)
