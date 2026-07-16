from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from app.infrastructure.security.tokens import generate_media_signature


@dataclass(frozen=True, slots=True)
class PublicMediaUrlBuilder:
    base_url: str | None
    signing_key: str

    def static_url(self, path: str) -> str:
        normalized = path.strip("/")
        if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
            raise ValueError("static media path is invalid")
        return f"{self._base_url()}/{quote(normalized, safe='/')}"

    def signed_image_url(
        self,
        asset_id: str,
        *,
        lifetime_seconds: int,
        now: datetime | None = None,
    ) -> str:
        instant = now or datetime.now(UTC)
        expires = int(instant.timestamp()) + lifetime_seconds
        signature = generate_media_signature(asset_id, expires, self.signing_key)
        query = urlencode({"expires": expires, "sig": signature})
        return f"{self._base_url()}/public/media/{asset_id}?{query}"

    def _base_url(self) -> str:
        return (self.base_url or "http://localhost:8000").rstrip("/")
