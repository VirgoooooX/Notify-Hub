"""HTTP client for calling the standalone Browser Publisher service."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class BrowserPublisherError(Exception):
    """Permanent error calling Browser Publisher (e.g. invalid params, rejected)."""


class BrowserPublisherTemporaryError(BrowserPublisherError):
    """Temporary or network error calling Browser Publisher (retryable)."""


class BrowserPublisherClient:
    """Client for Browser Publisher standalone HTTP API."""

    def __init__(
        self,
        base_url: str = "http://192.168.31.100:8790",
        access_token: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout

    async def submit_job(
        self,
        *,
        client_request_id: str,
        platform: str,
        mode: str | None = None,
        title: str,
        body_text: str,
        body_html: str | None = None,
        author: str | None = None,
        digest: str | None = None,
        image_urls: list[str] | None = None,
        topics: list[str] | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Submit a publishing job to Browser Publisher and return response."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        media = [{"kind": "url", "url": u} for u in (image_urls or [])]
        payload = {
            "client_request_id": client_request_id,
            "platform": platform,
            "mode": mode,
            "content": {
                "title": title,
                "body_text": body_text,
                "body_html": body_html,
                "author": author,
                "digest": digest,
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
                    return resp.json()

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
