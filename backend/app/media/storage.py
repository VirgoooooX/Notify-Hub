from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.media.errors import MediaError
from app.media.validation import ValidatedMedia


@dataclass(frozen=True, slots=True)
class StoredMedia:
    relative_path: str
    checksum_sha256: str
    size_bytes: int


class MediaStorage:
    """Filesystem storage that never accepts caller-selected local paths."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or not pure_path.parts:
            raise MediaError("unsafe_media_path", "Media path is outside controlled storage")
        candidate = self.root.joinpath(*pure_path.parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise MediaError("unsafe_media_path", "Media path is outside controlled storage")
        return candidate

    async def store(self, data: bytes, media: ValidatedMedia) -> StoredMedia:
        token = secrets.token_hex(24)
        relative_path = f"{media.kind.value}/{token[:2]}/{token}{media.extension}"
        destination = self.resolve(relative_path)
        temporary = destination.with_suffix(destination.suffix + f".{secrets.token_hex(8)}.tmp")

        def write() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with temporary.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(write)
        return StoredMedia(relative_path, hashlib.sha256(data).hexdigest(), len(data))

    async def read(self, relative_path: str, *, max_bytes: int) -> bytes:
        path = self.resolve(relative_path)

        def read_file() -> bytes:
            if not path.is_file() or path.stat().st_size > max_bytes:
                raise MediaError("media_unavailable", "Media is missing or exceeds its read limit")
            return path.read_bytes()

        return await asyncio.to_thread(read_file)

    async def delete(self, relative_path: str) -> bool:
        path = self.resolve(relative_path)

        def remove() -> bool:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

        return await asyncio.to_thread(remove)
