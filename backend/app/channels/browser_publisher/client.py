"""HTTP client for calling the standalone Browser Publisher service."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog

logger = structlog.get_logger()


class BrowserPublisherError(Exception):
    """Permanent error calling Browser Publisher (e.g. invalid params, rejected)."""


class BrowserPublisherTemporaryError(BrowserPublisherError):
    """Temporary or network error calling Browser Publisher (retryable)."""


def _url_origin(value: str) -> tuple[str, str, int] | None:
    """Return a normalized HTTP origin, or None for an unusable URL."""

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname.rstrip(".").lower(), port


class BrowserPublisherClient:
    """Client for Browser Publisher standalone HTTP API."""

    def __init__(
        self,
        base_url: str = "http://192.168.31.100:8790",
        access_token: str | None = None,
        timeout: float = 15.0,
        public_media_base_url: str | None = None,
        internal_media_base_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout
        self.public_media_base_url = (
            public_media_base_url.rstrip("/") if public_media_base_url else None
        )
        self.internal_media_base_url = (
            internal_media_base_url.rstrip("/") if internal_media_base_url else None
        )

    @property
    def configured(self) -> bool:
        """Whether the client has enough information to call the publisher."""

        return bool(self.base_url and self.access_token and self.access_token.strip())

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _rewrite_media_url(self, url: str) -> str:
        """Rewrite Notify Hub-owned media to a URL reachable by the publisher container."""

        if not self.public_media_base_url or not self.internal_media_base_url:
            return url

        source_origin = _url_origin(self.public_media_base_url)
        target_origin = _url_origin(self.internal_media_base_url)
        media_url = urlsplit(url)
        if source_origin is None or target_origin is None:
            return url
        if _url_origin(url) != source_origin:
            return url

        # Keep the path, query, and fragment (signed media URLs depend on the
        # query string), while replacing only the unreachable public origin.
        return urlunsplit(
            (
                target_origin[0],
                urlsplit(self.internal_media_base_url).netloc,
                media_url.path,
                media_url.query,
                media_url.fragment,
            )
        )

    async def upload_media(self, *, filename: str, content_type: str, content: bytes) -> str:
        """Upload one media asset and return its Browser Publisher media id."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/media",
                    files={"file": (filename, content, content_type)},
                    headers=self._auth_headers(),
                )
                if resp.status_code in (200, 201, 202):
                    try:
                        data = resp.json()
                    except ValueError as exc:
                        raise BrowserPublisherError(
                            "Browser Publisher returned invalid media response"
                        ) from exc
                    media_id = data.get("media_id")
                    if isinstance(media_id, str) and media_id:
                        return media_id
                    raise BrowserPublisherError(
                        "Browser Publisher media response did not contain media_id"
                    )

                if resp.status_code >= 500 or resp.status_code == 429:
                    raise BrowserPublisherTemporaryError(
                        f"Browser Publisher temporary error {resp.status_code}: {resp.text[:300]}"
                    )
                raise BrowserPublisherError(
                    f"Browser Publisher rejected media ({resp.status_code}): {resp.text[:300]}"
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BrowserPublisherTemporaryError(
                f"Browser Publisher media upload failed: {exc}"
            ) from exc

    async def submit_job(
        self,
        *,
        client_request_id: str,
        platform: str,
        mode: str | None = None,
        platform_draft_id: str | None = None,
        title: str,
        body_text: str,
        body_html: str | None = None,
        author: str | None = None,
        digest: str | None = None,
        visibility: str = "public",
        image_urls: list[str] | None = None,
        uploaded_media_ids: list[str] | None = None,
        topics: list[str] | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Submit a publishing job to Browser Publisher and return response."""
        headers = {"Content-Type": "application/json", **self._auth_headers()}

        media = [{"kind": "url", "url": self._rewrite_media_url(u)} for u in (image_urls or [])]
        media.extend(
            {"kind": "uploaded", "media_id": media_id} for media_id in (uploaded_media_ids or [])
        )
        payload = {
            "client_request_id": client_request_id,
            "platform": platform,
            "mode": mode,
            "platform_draft_id": platform_draft_id,
            "content": {
                "title": title,
                "body_text": body_text,
                "body_html": body_html,
                "author": author,
                "digest": digest,
                "visibility": visibility,
            },
            "media": media,
            "topics": topics or [],
            "source_url": source_url,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/jobs",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code in (200, 202):
                    return cast(dict[str, Any], resp.json())

                if resp.status_code >= 500 or resp.status_code == 429:
                    raise BrowserPublisherTemporaryError(
                        f"Browser Publisher temporary error {resp.status_code}: {resp.text[:300]}"
                    )
                raise BrowserPublisherError(
                    f"Browser Publisher rejected request ({resp.status_code}): {resp.text[:300]}"
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BrowserPublisherTemporaryError(
                f"Browser Publisher network request failed: {exc}"
            ) from exc
