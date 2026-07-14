import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from app.channels.base import ChannelResult
from app.config import Settings
from app.domain.clock import Clock

TOKEN_INVALID_CODES = {40014, 42001, 42007, 42009}
RETRYABLE_CODES = {-1, 45009}
AUTH_CODES = {40013, 40001, 40093}
RECIPIENT_CODES = {60111, 81013}
PAYLOAD_CODES = {40058, 40033, 40035, 44004}


@dataclass
class TokenCache:
    value: str | None = None
    expires_at: object | None = None


class WeComClient:
    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        timeout = httpx.Timeout(settings.wecom_request_timeout_seconds)
        self._http = http_client or httpx.AsyncClient(
            base_url=settings.wecom_api_base_url, timeout=timeout
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
            seconds=self._settings.wecom_token_refresh_skew_seconds
        )

    async def get_access_token(self, force: bool = False) -> str:
        if not force and self._cache_valid():
            return str(self._cache.value)
        async with self._lock:
            if not force and self._cache_valid():
                return str(self._cache.value)
            if not self._settings.wecom_corp_id or not self._settings.wecom_secret:
                raise RuntimeError("WeCom credentials are not configured")
            response = await self._http.get(
                "/cgi-bin/gettoken",
                params={
                    "corpid": self._settings.wecom_corp_id,
                    "corpsecret": self._settings.wecom_secret.get_secret_value(),
                },
            )
            response.raise_for_status()
            data = response.json()
            if data.get("errcode", 0) != 0 or not data.get("access_token"):
                raise RuntimeError("WeCom authentication failed")
            self._cache = TokenCache(
                value=str(data["access_token"]),
                expires_at=self._clock.now() + timedelta(seconds=int(data.get("expires_in", 7200))),
            )
            return str(self._cache.value)

    async def send(self, payload: dict[str, Any]) -> ChannelResult:
        try:
            token = await self.get_access_token()
            result = await self._post_message(token, payload)
            if result.response_metadata.get("errcode") in TOKEN_INVALID_CODES:
                token = await self.get_access_token(force=True)
                result = await self._post_message(token, payload)
            return result
        except (httpx.TimeoutException, httpx.NetworkError):
            return ChannelResult(False, True, "NETWORK_ERROR", "WeCom network request failed")
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500 or exc.response.status_code == 429
            return ChannelResult(
                False,
                retryable,
                "PROVIDER_TEMPORARY" if retryable else "UNKNOWN_PROVIDER_ERROR",
                f"WeCom HTTP error {exc.response.status_code}",
                provider_status=exc.response.status_code,
            )
        except RuntimeError as exc:
            return ChannelResult(False, False, "AUTH_INVALID", str(exc))

    async def upload_temporary_media(
        self, *, media_type: str, filename: str, mime_type: str, content: bytes
    ) -> str:
        token = await self.get_access_token()
        response = await self._http.post(
            "/cgi-bin/media/upload",
            params={"access_token": token, "type": media_type},
            files={"media": (filename, content, mime_type)},
        )
        response.raise_for_status()
        data = response.json()
        if int(data.get("errcode", 0)) != 0 or not data.get("media_id"):
            raise RuntimeError("WeCom temporary media upload failed")
        return str(data["media_id"])

    async def download_temporary_media(self, media_id: str, *, max_bytes: int) -> bytes:
        token = await self.get_access_token()
        output = bytearray()
        async with self._http.stream(
            "GET",
            "/cgi-bin/media/get",
            params={"access_token": token, "media_id": media_id},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if len(output) + len(chunk) > max_bytes:
                    raise ValueError("WeCom media exceeds configured size limit")
                output.extend(chunk)
        return bytes(output)

    async def _post_message(self, token: str, payload: dict[str, Any]) -> ChannelResult:
        response = await self._http.post(
            "/cgi-bin/message/send", params={"access_token": token}, json=payload
        )
        response.raise_for_status()
        data = response.json()
        code = int(data.get("errcode", -999))
        metadata = {"errcode": code}
        if code == 0:
            return ChannelResult(
                True,
                provider_message_id=str(data.get("msgid")) if data.get("msgid") else None,
                provider_status=response.status_code,
                response_metadata=metadata,
            )
        if code in TOKEN_INVALID_CODES or code in RETRYABLE_CODES:
            category, retryable = "PROVIDER_TEMPORARY", True
        elif code in AUTH_CODES:
            category, retryable = "AUTH_INVALID", False
        elif code in RECIPIENT_CODES:
            category, retryable = "RECIPIENT_INVALID", False
        elif code in PAYLOAD_CODES:
            category, retryable = "PAYLOAD_INVALID", False
        else:
            category, retryable = "UNKNOWN_PROVIDER_ERROR", False
        return ChannelResult(
            False,
            retryable,
            category,
            f"WeCom rejected the request (code {code})",
            provider_status=response.status_code,
            response_metadata=metadata,
        )
