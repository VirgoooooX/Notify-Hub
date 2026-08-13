from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from app.config import Settings
from app.domain.clock import Clock

TOKEN_INVALID_CODES = {40001, 42001}
RETRYABLE_CODES = {-1, 45009}
PAYLOAD_CODES = {40007, 40009, 40130, 41001, 41007, 53000, 53010}


class MPApiError(RuntimeError):
    """Stable provider error surfaced to the channel adapter."""

    def __init__(self, code: int, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class TokenCache:
    value: str | None = None
    expires_at: object | None = None


class MPClient:
    """WeChat Official Account (公众号) publishing client with a concurrent-safe token cache."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        timeout = httpx.Timeout(settings.mp_request_timeout_seconds)
        self._http = http_client or httpx.AsyncClient(
            base_url=settings.mp_api_base_url,
            timeout=timeout,
            follow_redirects=False,
        )
        self._owns_client = http_client is None
        self._cache = TokenCache()
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    def _cache_valid(self) -> bool:
        if self._cache.value is None or self._cache.expires_at is None:
            return False
        return self._cache.expires_at > self._clock.now() + timedelta(  # type: ignore[operator]
            seconds=self._settings.mp_token_refresh_skew_seconds
        )

    async def get_access_token(self, force: bool = False) -> str:
        if not force and self._cache_valid():
            return str(self._cache.value)
        async with self._lock:
            if not force and self._cache_valid():
                return str(self._cache.value)
            if not self._settings.mp_app_id or not self._settings.mp_app_secret:
                raise RuntimeError("MP credentials are not configured")
            response = await self._http.get(
                "cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self._settings.mp_app_id,
                    "secret": self._settings.mp_app_secret.get_secret_value(),
                },
            )
            response.raise_for_status()
            data = response.json()
            if int(data.get("errcode", 0)) != 0 or not data.get("access_token"):
                raise RuntimeError("MP authentication failed")
            self._cache = TokenCache(
                value=str(data["access_token"]),
                expires_at=self._clock.now() + timedelta(seconds=int(data.get("expires_in", 7200))),
            )
            return str(self._cache.value)

    async def upload_permanent_image(
        self, *, filename: str, content_type: str, content: bytes
    ) -> str:
        data, _ = await self._request_json(
            "POST",
            "cgi-bin/material/add_material",
            params={"type": "image"},
            files={"media": (filename, content, content_type)},
        )
        media_id = data.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise MPApiError(0, "MP did not return a media id")
        return media_id

    async def add_draft(self, articles: list[dict[str, Any]]) -> str:
        data, _ = await self._request_json("POST", "cgi-bin/draft/add", json={"articles": articles})
        media_id = data.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise MPApiError(0, "MP did not return a draft media id")
        return media_id

    async def submit_publish(self, media_id: str) -> str:
        data, _ = await self._request_json(
            "POST", "cgi-bin/freepublish/submit", json={"media_id": media_id}
        )
        publish_id = data.get("publish_id")
        if publish_id is None:
            raise MPApiError(0, "MP did not return a publish id")
        return str(publish_id)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> tuple[dict[str, Any], httpx.Response]:
        token = await self.get_access_token()
        request_params = {**(params or {}), "access_token": token}
        response = await self._http.request(
            method, path, params=request_params, json=json, files=files
        )
        response.raise_for_status()
        data = response.json()
        if int(data.get("errcode", 0)) in TOKEN_INVALID_CODES:
            token = await self.get_access_token(force=True)
            request_params = {**(params or {}), "access_token": token}
            response = await self._http.request(
                method, path, params=request_params, json=json, files=files
            )
            response.raise_for_status()
            data = response.json()
        code = int(data.get("errcode", 0))
        if code != 0:
            message = str(data.get("errmsg") or "MP API rejected the request")
            raise MPApiError(
                code,
                message,
                retryable=code in RETRYABLE_CODES,
            )
        return data, response
