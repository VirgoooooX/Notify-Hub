"""HTTP client for communicating with the Notify Hub MP Browser API."""

from __future__ import annotations

from typing import Any, cast

import httpx

from app.mp_browser.config import MPBrowserSettings


class MPBrowserApiClient:
    """Interacts with Notify Hub backend using an authorized ApiClient key."""

    def __init__(self, settings: MPBrowserSettings) -> None:
        self._base_url = settings.api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={
                "X-API-Key": settings.api_key.get_secret_value(),
                "User-Agent": "NotifyHub-MPBrowser/1.0",
            },
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )

    async def claim(self) -> dict[str, Any] | None:
        """Claim the next available article from the publishing queue (FIFO)."""
        response = await self._client.post(f"{self._base_url}/claim")
        if response.status_code == 204:
            return None
        response.raise_for_status()
        body = response.json()
        return cast(dict[str, Any] | None, body.get("data"))

    async def checkpoint(
        self,
        article_id: str,
        *,
        phase: str,
        draft_url: str | None = None,
        provider_draft_id: str | None = None,
    ) -> dict[str, Any]:
        """Report article phase checkpoint to backend."""
        response = await self._client.post(
            f"{self._base_url}/articles/{article_id}/checkpoint",
            json={
                "phase": phase,
                "draft_url": draft_url,
                "provider_draft_id": provider_draft_id,
            },
        )
        response.raise_for_status()
        body = response.json()
        return cast(dict[str, Any], body.get("data"))

    async def complete(
        self,
        article_id: str,
        *,
        status: str = "published",
        provider_draft_id: str | None = None,
        provider_publish_id: str | None = None,
        published_url: str | None = None,
    ) -> dict[str, Any]:
        """Mark article publication as completed (or draft saved)."""
        response = await self._client.post(
            f"{self._base_url}/articles/{article_id}/complete",
            json={
                "status": status,
                "provider_draft_id": provider_draft_id,
                "provider_publish_id": provider_publish_id,
                "published_url": published_url,
            },
        )
        response.raise_for_status()
        body = response.json()
        return cast(dict[str, Any], body.get("data"))

    async def fail(
        self,
        article_id: str,
        *,
        retryable: bool,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        """Report article failure."""
        response = await self._client.post(
            f"{self._base_url}/articles/{article_id}/fail",
            json={
                "retryable": retryable,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        response.raise_for_status()
        body = response.json()
        return cast(dict[str, Any], body.get("data"))

    async def release_for_auth(self, article_id: str) -> dict[str, Any]:
        """Release claimed article due to auth expiration without attempt penalty."""
        response = await self._client.post(f"{self._base_url}/articles/{article_id}/release-auth")
        response.raise_for_status()
        body = response.json()
        return cast(dict[str, Any], body.get("data"))

    async def update_session(
        self,
        *,
        state: str,
        current_article_id: str | None = None,
        incident_id: str | None = None,
        last_error_code: str | None = None,
        last_error_message: str | None = None,
        qr_png_base64: str | None = None,
    ) -> dict[str, Any]:
        """Report publisher session heartbeat and QR image."""
        response = await self._client.post(
            f"{self._base_url}/session",
            json={
                "state": state,
                "current_article_id": current_article_id,
                "incident_id": incident_id,
                "last_error_code": last_error_code,
                "last_error_message": last_error_message,
                "qr_png_base64": qr_png_base64,
            },
        )
        response.raise_for_status()
        body = response.json()
        return cast(dict[str, Any], body.get("data"))

    async def close(self) -> None:
        await self._client.aclose()
