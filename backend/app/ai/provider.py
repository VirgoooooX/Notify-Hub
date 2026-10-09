from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.ai.schemas import AIHubProfile
from app.infrastructure.database.ai_models import AIProvider

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[set[Address]]]


class AIProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


async def system_resolver(host: str, port: int) -> set[Address]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass

    def lookup() -> set[Address]:
        return {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }

    try:
        return await asyncio.to_thread(lookup)
    except socket.gaierror as exc:
        raise AIProviderError(
            "ai_dns_failed", "AI provider host could not be resolved", retryable=True
        ) from exc


def _forbidden(address: Address) -> bool:
    return not address.is_global or address in {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
    }


def _always_forbidden(address: Address) -> bool:
    return (
        address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address
        in {
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("100.100.100.200"),
        }
    )


class AIHubClient:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = 1024 * 1024,
        max_catalog_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._resolver = resolver
        self._client = client
        self._max_response_bytes = max_response_bytes
        self._max_catalog_response_bytes = max_catalog_response_bytes

    async def _validate_url(self, url: str, allow_private_network: bool) -> set[Address]:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AIProviderError("ai_unsafe_url", "AI provider URL is invalid")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise AIProviderError("ai_unsafe_url", "AI provider URL contains forbidden parts")
        if parsed.scheme != "https" and not allow_private_network:
            raise AIProviderError("ai_unsafe_url", "AI provider requires HTTPS")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await self._resolver(parsed.hostname, port)
        if not addresses:
            raise AIProviderError("ai_dns_failed", "AI provider host returned no address")
        if not allow_private_network and any(_forbidden(address) for address in addresses):
            raise AIProviderError("ai_unsafe_url", "AI provider resolves to a forbidden address")
        if any(_always_forbidden(address) for address in addresses):
            raise AIProviderError("ai_unsafe_url", "Cloud metadata addresses are forbidden")
        return addresses

    def _validate_peer(
        self, response: httpx.Response, expected: set[Address], allow_private_network: bool
    ) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None:
            if self._resolver is system_resolver:
                raise AIProviderError("ai_unsafe_url", "AI provider peer could not be verified")
            return
        server = stream.get_extra_info("server_addr")
        if not server:
            raise AIProviderError("ai_unsafe_url", "AI provider peer could not be verified")
        try:
            peer = ipaddress.ip_address(server[0])
        except ValueError as exc:
            raise AIProviderError("ai_unsafe_url", "AI provider peer is invalid") from exc
        if (
            peer not in expected
            or _always_forbidden(peer)
            or (not allow_private_network and _forbidden(peer))
        ):
            raise AIProviderError("ai_unsafe_url", "AI provider address changed after validation")

    @asynccontextmanager
    async def _http_client(self, verify_tls: bool) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            follow_redirects=False, verify=verify_tls, trust_env=False
        ) as client:
            yield client

    async def _request(
        self,
        connection: AIProvider,
        *,
        application_key: str | None,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if not application_key:
            raise AIProviderError("ai_hub_key_missing", "AI Hub application key is not configured")
        endpoint = f"{connection.base_url.rstrip('/')}/api/v1/{path}"
        try:
            async with asyncio.timeout(timeout_seconds + 10):
                expected = await self._validate_url(endpoint, connection.allow_private_network)
                async with (
                    self._http_client(connection.verify_tls) as client,
                    client.stream(
                        "POST" if body is not None else "GET",
                        endpoint,
                        headers={"Authorization": f"Bearer {application_key}"},
                        json=body,
                        follow_redirects=False,
                        timeout=httpx.Timeout(
                            timeout_seconds + 10, connect=min(5.0, timeout_seconds)
                        ),
                    ) as response,
                ):
                    self._validate_peer(response, expected, connection.allow_private_network)
                    if response.is_redirect:
                        raise AIProviderError(
                            "ai_redirect_forbidden", "AI Hub redirects are forbidden"
                        )
                    raw = bytearray()
                    limit = (
                        self._max_response_bytes
                        if body is not None
                        else self._max_catalog_response_bytes
                    )
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > limit:
                            raise AIProviderError(
                                "ai_response_too_large", "AI Hub response is too large"
                            )
                    if response.is_error:
                        # Remote messages may contain secrets; expose only a bounded machine code.
                        code = "http_error"
                        retryable = response.status_code == 429 or response.status_code >= 500
                        try:
                            error = json.loads(raw)["error"]
                            candidate = error.get("code")
                            if isinstance(candidate, str) and re.fullmatch(
                                r"[a-z][a-z0-9_]{0,70}", candidate
                            ):
                                code = candidate
                            if type(error.get("retryable")) is bool:
                                retryable = error["retryable"]
                        except (ValueError, KeyError, TypeError, AttributeError):
                            pass
                        raise AIProviderError(
                            f"ai_hub_{code}", "AI Hub request failed", retryable=retryable
                        )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise AIProviderError("ai_timeout", "AI Hub timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise AIProviderError(
                "ai_network_error", "AI Hub request failed", retryable=True
            ) from exc
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError
            return data
        except (TypeError, ValueError) as exc:
            raise AIProviderError("ai_invalid_response", "AI Hub response is invalid") from exc

    async def generate(
        self,
        connection: AIProvider,
        *,
        application_key: str | None,
        task: str,
        messages: list[dict[str, str]],
        output: dict[str, Any],
        parameters: dict[str, Any],
    ) -> tuple[str, int | None, int | None]:
        data = await self._request(
            connection,
            application_key=application_key,
            path="generate",
            body={"task": task, "messages": messages, "output": output, "parameters": parameters},
            timeout_seconds=connection.timeout_seconds,
        )
        try:
            content = data["output"]["text"]
            usage = data["usage"]
            if not isinstance(content, str) or not content or not isinstance(usage, dict):
                raise TypeError
            tokens = [usage.get(key) for key in ("input_tokens", "output_tokens")]
            if any(value is not None and (type(value) is not int or value < 0) for value in tokens):
                raise TypeError
            return content, tokens[0], tokens[1]
        except (KeyError, TypeError) as exc:
            raise AIProviderError("ai_invalid_response", "AI Hub response is invalid") from exc

    async def list_models(
        self, connection: AIProvider, *, application_key: str | None
    ) -> list[str]:
        data = await self._request(
            connection,
            application_key=application_key,
            path="models",
            timeout_seconds=min(connection.timeout_seconds, 30),
        )
        try:
            entries = data["data"]
            if not isinstance(entries, list):
                raise TypeError
            ids = [entry["id"] for entry in entries]
            if any(not isinstance(model_id, str) or not model_id for model_id in ids):
                raise TypeError
            return ids
        except (KeyError, TypeError) as exc:
            raise AIProviderError("ai_invalid_response", "AI Hub model list is invalid") from exc

    async def list_profiles(
        self, connection: AIProvider, *, application_key: str | None
    ) -> list[AIHubProfile]:
        data = await self._request(
            connection,
            application_key=application_key,
            path="profiles",
            timeout_seconds=min(connection.timeout_seconds, 15),
        )
        try:
            if not isinstance(data["data"], list):
                raise TypeError
            profiles = [AIHubProfile.model_validate(item) for item in data["data"]]
            if len({profile.id for profile in profiles}) != len(profiles):
                raise ValueError
            return profiles
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise AIProviderError("ai_invalid_response", "AI Hub Profile list is invalid") from exc
