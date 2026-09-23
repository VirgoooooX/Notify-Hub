from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

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


class OpenAICompatibleClient:
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

    async def complete(
        self,
        provider: AIProvider,
        *,
        api_key: str | None,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_output_tokens: int,
        reasoning_effort: str = "provider_default",
        reasoning_capable: bool = False,
        response_format: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> tuple[str, int | None, int | None]:
        if provider.protocol != "openai_chat_completions":
            raise AIProviderError(
                "ai_protocol_unsupported", "AI provider protocol is not supported"
            )
        endpoint = f"{provider.base_url.rstrip('/')}/chat/completions"
        expected = await self._validate_url(endpoint, provider.allow_private_network)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if reasoning_effort == "provider_default" and not reasoning_capable:
            payload.update(temperature=temperature, max_tokens=max_output_tokens)
        else:
            payload["max_completion_tokens"] = max_output_tokens
            if reasoning_effort != "provider_default":
                payload["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            payload["response_format"] = response_format
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        try:
            async with (
                self._http_client(provider.verify_tls) as client,
                client.stream(
                    "POST",
                    endpoint,
                    headers=headers,
                    params=provider.custom_query,
                    content=body,
                    follow_redirects=False,
                    timeout=httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds)),
                ) as response,
            ):
                self._validate_peer(response, expected, provider.allow_private_network)
                if response.is_redirect:
                    raise AIProviderError(
                        "ai_redirect_forbidden", "AI provider redirects are forbidden"
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > self._max_response_bytes:
                        raise AIProviderError(
                            "ai_response_too_large", "AI provider response is too large"
                        )
                if response.status_code >= 400:
                    retryable = response.status_code == 429 or response.status_code >= 500
                    code = (
                        "ai_structured_output_unsupported"
                        if response.status_code in {400, 422} and response_format is not None
                        else "ai_provider_http_error"
                    )
                    raise AIProviderError(
                        code, "AI provider returned an error", retryable=retryable
                    )
        except httpx.TimeoutException as exc:
            raise AIProviderError("ai_timeout", "AI provider timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise AIProviderError(
                "ai_network_error", "AI provider request failed", retryable=True
            ) from exc
        try:
            response_data = json.loads(raw)
            content = response_data["choices"][0]["message"]["content"]
            usage = response_data.get("usage") or {}
            if not isinstance(content, str):
                raise TypeError
            return content, usage.get("prompt_tokens"), usage.get("completion_tokens")
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("ai_invalid_response", "AI provider response is invalid") from exc

    async def list_models(self, provider: AIProvider, *, api_key: str | None) -> list[str]:
        catalog = await self.list_model_catalog(provider, api_key=api_key, include_metadata=False)
        return [model["model_id"] for model in catalog]

    async def list_model_catalog(
        self, provider: AIProvider, *, api_key: str | None, include_metadata: bool = True
    ) -> list[dict[str, Any]]:
        endpoint = f"{provider.base_url.rstrip('/')}/models"
        expected = await self._validate_url(endpoint, provider.allow_private_network)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with self._http_client(provider.verify_tls) as client:
                standard = await self._fetch_model_list(
                    client, provider, endpoint, expected, headers, catalog_request=False
                )
                catalog = (
                    await self._fetch_model_list(
                        client, provider, endpoint, expected, headers, catalog_request=True
                    )
                    if include_metadata
                    else None
                )
        except httpx.TimeoutException as exc:
            raise AIProviderError("ai_timeout", "AI provider timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise AIProviderError(
                "ai_network_error", "AI provider request failed", retryable=True
            ) from exc
        try:
            if standard is None:
                raise TypeError
            standard_entries = standard["data"]
            if not isinstance(standard_entries, list):
                raise TypeError
            models = self._parse_model_entries(standard_entries, id_field="id")
            if catalog is not None and isinstance(catalog.get("models"), list):
                extended = self._parse_model_entries(catalog["models"], id_field="slug")
                for model_id, model in models.items():
                    if model_id in extended:
                        model.update(extended[model_id])
            return [models[model_id] for model_id in sorted(models)]
        except (KeyError, TypeError) as exc:
            raise AIProviderError("ai_invalid_response", "AI model list is invalid") from exc

    async def _fetch_model_list(
        self,
        client: httpx.AsyncClient,
        provider: AIProvider,
        endpoint: str,
        expected: set[Address],
        headers: dict[str, str],
        *,
        catalog_request: bool,
    ) -> dict[str, Any] | None:
        params = dict(provider.custom_query)
        if catalog_request:
            params["client_version"] = "pi"
        async with client.stream(
            "GET",
            endpoint,
            headers=headers,
            params=params,
            follow_redirects=False,
            timeout=httpx.Timeout(
                provider.timeout_seconds,
                connect=min(5.0, provider.timeout_seconds),
            ),
        ) as response:
            self._validate_peer(response, expected, provider.allow_private_network)
            if response.is_redirect:
                raise AIProviderError(
                    "ai_redirect_forbidden", "AI provider redirects are forbidden"
                )
            if catalog_request and response.status_code in {400, 401, 403, 404, 422}:
                return None
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > self._max_catalog_response_bytes:
                    raise AIProviderError(
                        "ai_response_too_large", "AI provider response is too large"
                    )
            if response.status_code >= 400:
                raise AIProviderError(
                    "ai_provider_http_error",
                    "AI provider returned an error",
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError
            return data
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("ai_invalid_response", "AI model list is invalid") from exc

    @staticmethod
    def _parse_model_entries(entries: list[Any], *, id_field: str) -> dict[str, dict[str, Any]]:
        models: dict[str, dict[str, Any]] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            model_id = item.get(id_field)
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            raw_levels = item.get("supported_reasoning_levels")
            levels: list[str] | None = None
            if isinstance(raw_levels, list):
                levels = list(
                    dict.fromkeys(
                        effort
                        for level in raw_levels[:32]
                        if isinstance(level, dict)
                        and isinstance((effort := level.get("effort")), str)
                        and re.fullmatch(r"[a-z][a-z0-9_-]{0,29}", effort)
                    )
                )[:16]
            elif "supported_reasoning_levels" in item:
                levels = []
            default = item.get("default_reasoning_level")
            if not isinstance(default, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,29}", default):
                default = None
            models[model_id] = {
                "model_id": model_id,
                "supported_reasoning_levels": levels,
                "default_reasoning_level": default,
            }
        if entries and not models:
            raise TypeError
        return models
