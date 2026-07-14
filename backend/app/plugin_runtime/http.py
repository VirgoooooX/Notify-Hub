from __future__ import annotations

import ipaddress
import socket
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import urljoin, urlsplit

import anyio
import httpx


class RestrictedHttpError(RuntimeError):
    pass


class ResponseTooLarge(RestrictedHttpError):
    pass


@dataclass(frozen=True)
class RestrictedResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    url: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.content)


class RestrictedHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: list[str],
        allowed_private_networks: list[str] | None = None,
        timeout_seconds: float = 20.0,
        connect_timeout_seconds: float = 5.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self._private_networks = [
            ipaddress.ip_network(item, strict=True) for item in (allowed_private_networks or [])
        ]
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
            follow_redirects=False,
            verify=True,
        )

    async def __aenter__(self) -> RestrictedHttpClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _host_allowed(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(
            host == allowed or host.endswith(f".{allowed}") for allowed in self._allowed_hosts
        )

    def _address_allowed(self, address: str) -> bool:
        ip = ipaddress.ip_address(address)
        if any(ip in network for network in self._private_networks):
            return True
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    async def validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise RestrictedHttpError("only http and https URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise RestrictedHttpError("URL credentials are not allowed")
        if not parsed.hostname or not self._host_allowed(parsed.hostname):
            raise RestrictedHttpError("target host is not permitted")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            try:
                addresses = await anyio.to_thread.run_sync(
                    partial(
                        socket.getaddrinfo,
                        parsed.hostname,
                        port,
                        family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM,
                    )
                )
            except OSError as exc:
                raise RestrictedHttpError("target DNS resolution failed") from exc
            if not addresses:
                raise RestrictedHttpError("target DNS returned no address") from None
            if any(not self._address_allowed(str(item[4][0])) for item in addresses):
                raise RestrictedHttpError("target resolves to a forbidden address") from None
        else:
            if not self._address_allowed(str(literal)):
                raise RestrictedHttpError("target address is forbidden")

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        content: bytes | str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - plugin contract uses this keyword.
    ) -> RestrictedResponse:
        effective_timeout = (
            self._timeout_seconds if timeout is None else min(timeout, self._timeout_seconds)
        )
        if effective_timeout <= 0:
            raise RestrictedHttpError("timeout must be positive")
        current = url
        for redirect_count in range(self._max_redirects + 1):
            await self.validate_url(current)
            async with self._client.stream(
                method,
                current,
                headers=headers,
                params=params,
                content=content,
                timeout=effective_timeout,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if location is None:
                        raise RestrictedHttpError("redirect did not include a location")
                    if redirect_count >= self._max_redirects:
                        raise RestrictedHttpError("too many redirects")
                    current = urljoin(str(response.url), location)
                    continue
                body = bytearray()
                async for chunk in self._limited_bytes(response.aiter_bytes()):
                    body.extend(chunk)
                return RestrictedResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content=bytes(body),
                    url=str(response.url),
                )
        raise RestrictedHttpError("too many redirects")

    async def _limited_bytes(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in chunks:
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ResponseTooLarge("response exceeded the configured size limit")
            yield chunk

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - plugin contract uses this keyword.
    ) -> RestrictedResponse:
        return await self.request("GET", url, headers=headers, params=params, timeout=timeout)
