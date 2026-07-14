from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from app.media.errors import MediaError, MediaTooLargeError, UnsafeMediaUrlError

Resolver = Callable[[str, int], Awaitable[set[ipaddress.IPv4Address | ipaddress.IPv6Address]]]


async def system_resolver(
    host: str, port: int
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(host)
        return {literal}
    except ValueError:
        pass

    def lookup() -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return {ipaddress.ip_address(result[4][0]) for result in results}

    try:
        return await asyncio.to_thread(lookup)
    except socket.gaierror as exc:
        raise MediaError(
            "media_dns_failed", "Media host could not be resolved", retryable=True
        ) from exc


def _is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not address.is_global or address in {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
    }


class SafeMediaDownloader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        resolver: Resolver = system_resolver,
        timeout_seconds: float = 10.0,
        max_redirects: int = 3,
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_redirects = max_redirects

    async def _validate_url(self, url: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeMediaUrlError("Only absolute HTTP(S) media URLs are accepted")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeMediaUrlError("Credentials in media URLs are forbidden")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await self._resolver(parsed.hostname, port)
        if not addresses or any(_is_forbidden(address) for address in addresses):
            raise UnsafeMediaUrlError()
        return addresses

    def _validate_peer(
        self,
        response: httpx.Response,
        expected: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None:
            if self._resolver is system_resolver:
                raise UnsafeMediaUrlError("Media connection address could not be verified")
            return
        server = stream.get_extra_info("server_addr")
        if not server:
            raise UnsafeMediaUrlError("Media connection address could not be verified")
        try:
            peer = ipaddress.ip_address(server[0])
        except ValueError as exc:
            raise UnsafeMediaUrlError("Media connection address is invalid") from exc
        if peer not in expected or _is_forbidden(peer):
            raise UnsafeMediaUrlError("Media connection address changed after validation")

    async def download(self, url: str, *, max_bytes: int) -> bytes:
        current = url
        for redirect_count in range(self._max_redirects + 1):
            expected_addresses = await self._validate_url(current)
            try:
                async with self._client.stream(
                    "GET", current, follow_redirects=False, timeout=self._timeout
                ) as response:
                    self._validate_peer(response, expected_addresses)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= self._max_redirects:
                            raise MediaError("too_many_redirects", "Media redirect limit exceeded")
                        location = response.headers.get("location")
                        if not location:
                            raise MediaError("invalid_redirect", "Media redirect has no location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length is not None:
                        try:
                            if int(length) > max_bytes:
                                raise MediaTooLargeError(max_bytes)
                        except ValueError as exc:
                            raise MediaError(
                                "invalid_media_response", "Invalid Content-Length header"
                            ) from exc
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(output) + len(chunk) > max_bytes:
                            raise MediaTooLargeError(max_bytes)
                        output.extend(chunk)
                    return bytes(output)
            except httpx.TimeoutException as exc:
                raise MediaError(
                    "media_download_timeout", "Media download timed out", retryable=True
                ) from exc
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code >= 500 or exc.response.status_code == 429
                raise MediaError(
                    "media_download_failed", "Media server returned an error", retryable=retryable
                ) from exc
            except httpx.RequestError as exc:
                raise MediaError(
                    "media_download_failed", "Media download failed", retryable=True
                ) from exc
        raise MediaError("too_many_redirects", "Media redirect limit exceeded")
