from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from app.ai.provider import AIHubClient, AIProviderError
from app.ai.schemas import AIClassificationItem
from app.ai.service import AIGatewayError, AIService
from app.application.ai_control_service import HUB_CONNECTION_ID, AIControlService
from app.infrastructure.database import Base
from app.infrastructure.database.ai_models import (
    AIInvocation,
    AIProfile,
    AIProvider,
    AIResponseCache,
)
from app.infrastructure.database.session import create_session_factory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine


async def _public_resolver(_host: str, _port: int) -> set[ipaddress.IPv4Address]:
    return {ipaddress.ip_address("93.184.216.34")}


async def _configured_service(
    tmp_path: Path,
    handler: httpx.MockTransport,
    *,
    daily_request_limit: int | None = 10,
) -> tuple[AIService, object, httpx.AsyncClient]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ai-gateway.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    control = AIControlService(factory)
    await control.update_connection(
        {
            "base_url": "https://hub.example.test",
            "enabled": True,
            "allow_private_network": False,
            "timeout_seconds": 600,
            "verify_tls": True,
        }
    )
    async with factory() as session, session.begin():
        session.add(
            AIProfile(
                id="test_classifier",
                name="Test classifier",
                provider_id=HUB_CONNECTION_ID,
                model="",
                capability="classify",
                temperature=0,
                max_output_tokens=160,
                response_format="auto",
                timeout_seconds=5,
                cache_ttl_seconds=3600,
                daily_request_limit=daily_request_limit,
                daily_token_limit=None,
                enabled=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

    async def catalog_transport(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/profiles"):
            async with factory() as session:
                rows = list(
                    await session.scalars(select(AIProfile).where(AIProfile.deleted_at.is_(None)))
                )
                entries = []
                for row in rows:
                    view = control._profile_view(row)
                    metadata = {
                        key: view[key]
                        for key in [
                            "name",
                            "description",
                            "enabled",
                            "response_format",
                            "output_language",
                            "verbosity",
                            "include_reason",
                            "max_reason_characters",
                            "cache_ttl_seconds",
                            "daily_request_limit",
                            "daily_token_limit",
                        ]
                    }
                    entry = {
                        "id": row.id,
                        "purpose": row.capability,
                        "available": True,
                        "protocol": "chat",
                        "models": ["synthetic"],
                        **metadata,
                        "parameters": {
                            "temperature": row.temperature,
                            "max_output_tokens": row.max_output_tokens,
                            "reasoning_effort": None
                            if row.reasoning_effort == "provider_default"
                            else row.reasoning_effort,
                            "timeout_seconds": row.timeout_seconds,
                        },
                    }
                    import hashlib

                    entry["revision"] = hashlib.sha256(
                        json.dumps(entry, sort_keys=True).encode()
                    ).hexdigest()
                    entries.append(entry)
            return httpx.Response(200, json={"data": entries})
        return await handler.handle_async_request(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(catalog_transport))
    hub_client = AIHubClient(resolver=_public_resolver, client=client)
    secrets = AsyncMock()
    secrets.get.return_value = "test-application-key"
    return AIService(factory, hub_client=hub_client, secret_store=secrets), factory, client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "invalid_application_key", False),
        (422, "output_schema_mismatch", False),
        (429, "upstream_http_error", True),
        (504, "upstream_timeout", True),
    ],
)
async def test_hub_errors_fail_closed_without_local_retry_or_sensitive_body(
    tmp_path: Path, status: int, code: str, retryable: bool
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            json={
                "error": {"code": code, "retryable": retryable, "message": "private-secret-body"},
            },
        )

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(AIGatewayError) as error:
        await service.classify(
            profile="test_classifier",
            plugin_id="plugin",
            plugin_run_id=None,
            use_case="failure",
            content="synthetic",
            instruction="Classify.",
            labels=["notify", "ignore"],
        )
    assert error.value.code == f"ai_hub_{code}"
    assert error.value.retryable is retryable
    assert "private-secret-body" not in str(error.value)
    assert calls == 1
    async with factory() as session:
        row = await session.scalar(select(AIInvocation))
        assert row.status == "failed" and row.error_code == f"ai_hub_{code}"
        assert await session.scalar(select(func.count(AIResponseCache.id))) == 0
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_hub_network_timeout_does_not_retry(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private body", request=request)

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(AIGatewayError, match="AI Hub timed out") as error:
        await service.classify(
            profile="test_classifier",
            plugin_id=None,
            plugin_run_id=None,
            use_case="timeout",
            content="synthetic",
            instruction="Classify.",
            labels=["notify", "ignore"],
        )
    assert error.value.code == "ai_timeout" and calls == 1
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_missing_hub_key_never_uses_legacy_provider_key(tmp_path: Path) -> None:
    service, factory, client = await _configured_service(
        tmp_path, httpx.MockTransport(lambda request: pytest.fail("must not send a request"))
    )
    service._secrets.get.return_value = None
    with pytest.raises(AIGatewayError) as error:
        await service.classify(
            profile="test_classifier",
            plugin_id=None,
            plugin_run_id=None,
            use_case="missing_key",
            content="synthetic",
            instruction="Classify.",
            labels=["notify", "ignore"],
        )
    assert error.value.code == "ai_hub_key_missing"
    service._secrets.get.assert_awaited_once_with("ai_hub", HUB_CONNECTION_ID, "application_key")
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address,allow_private,expected",
    [
        ("127.0.0.1", False, "ai_unsafe_url"),
        ("169.254.169.254", True, "ai_unsafe_url"),
        ("100.100.100.200", True, "ai_unsafe_url"),
    ],
)
async def test_hub_blocks_private_and_metadata_addresses(
    address: str, allow_private: bool, expected: str
) -> None:
    async def resolver(_host: str, _port: int) -> set[ipaddress.IPv4Address]:
        return {ipaddress.ip_address(address)}

    client = AIHubClient(resolver=resolver)
    with pytest.raises(AIProviderError) as error:
        await client._validate_url("https://hub.example.test", allow_private)
    assert error.value.code == expected


@pytest.mark.asyncio
async def test_hub_allows_explicit_private_network_and_rejects_redirects(tmp_path: Path) -> None:
    service, factory, client = await _configured_service(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={"Location": "https://elsewhere.example.test"}
            )
        ),
    )
    assert await service._hub_client._validate_url("http://127.0.0.1:8848", True)
    with pytest.raises(AIGatewayError) as error:
        await service.test_connection()
    assert error.value.code == "ai_redirect_forbidden"
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_hub_connection_change_invalidates_persistent_business_cache(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success_response(request)

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    options = dict(
        profile="test_classifier",
        plugin_id=None,
        plugin_run_id=None,
        use_case="cache",
        content="synthetic",
        instruction="Classify.",
        labels=["notify", "ignore"],
    )
    await service.classify(**options)
    control = AIControlService(factory)
    await control.update_connection(
        {**await control.get_connection(), "base_url": "https://new-hub.example.test"}
    )
    await service.classify(**options)
    assert calls == 2
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("text,calls", [("[]", 1), ("private-invalid-json", 2)])
async def test_hub_malformed_business_output_has_bounded_repair_and_no_cache(
    tmp_path: Path, text: str, calls: int
) -> None:
    received = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received
        received += 1
        return httpx.Response(200, json={"output": {"text": text}, "usage": {}})

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(AIGatewayError) as error:
        await service.classify(
            profile="test_classifier",
            plugin_id=None,
            plugin_run_id=None,
            use_case="malformed",
            content="synthetic",
            instruction="Classify.",
            labels=["notify", "ignore"],
        )
    assert error.value.code == "ai_invalid_structured_output"
    assert "private-invalid-json" not in str(error.value)
    assert received == calls
    async with factory() as session:
        row = await session.scalar(select(AIInvocation))
        assert row.status == "failed"
        assert await session.scalar(select(func.count(AIResponseCache.id))) == 0
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_hub_bounds_streamed_response_size(tmp_path: Path) -> None:
    service, factory, client = await _configured_service(
        tmp_path, httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1001))
    )
    service._hub_client._max_response_bytes = 1000
    with pytest.raises(AIGatewayError) as error:
        await service.classify(
            profile="test_classifier",
            plugin_id=None,
            plugin_run_id=None,
            use_case="size",
            content="synthetic",
            instruction="Classify.",
            labels=["notify", "ignore"],
        )
    assert error.value.code == "ai_response_too_large"
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.parametrize("peer", ["127.0.0.1", "93.184.216.35"])
def test_hub_peer_must_match_the_validated_address(peer: str) -> None:
    class Stream:
        def get_extra_info(self, name: str) -> tuple[str, int]:
            return peer, 443

    client = AIHubClient()
    response = httpx.Response(200, extensions={"network_stream": Stream()})
    with pytest.raises(AIProviderError) as error:
        client._validate_peer(response, {ipaddress.ip_address("93.184.216.34")}, False)
    assert error.value.code == "ai_unsafe_url"


def _success_response(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/api/v1/generate"
    assert request.headers["authorization"] == "Bearer test-application-key"
    payload = json.loads(request.content)
    assert payload["task"] == "test_classifier"
    assert set(payload) == {"task", "messages", "output", "parameters"}
    user_data = json.loads(payload["messages"][1]["content"])
    results = [
        {"id": item["id"], "label": "notify", "confidence": 0.95, "reason": "match"}
        for item in user_data["items"]
    ]
    return httpx.Response(
        200,
        json={
            "request_id": "hub-request",
            "model": "center-selected-model",
            "output": {
                "type": "json_schema",
                "text": json.dumps({"results": results}),
                "data": {"results": results},
            },
            "usage": {"input_tokens": 20, "output_tokens": 10},
        },
    )


@pytest.mark.asyncio
async def test_ai_gateway_batches_and_reuses_persistent_cache(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success_response(request)

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    items = [
        AIClassificationItem(id="post-1", content="Codex quota reset"),
        AIClassificationItem(id="post-2", content="ChatGPT limit restored"),
    ]
    first = await service.classify_many(
        profile="test_classifier",
        plugin_id="codex_x_monitor",
        plugin_run_id="run-1",
        use_case="codex_usage_reset",
        instruction="Classify whether usage limits recovered.",
        labels=["notify", "ignore", "uncertain"],
        items=items,
    )
    second = await service.classify_many(
        profile="test_classifier",
        plugin_id="codex_x_monitor",
        plugin_run_id="run-2",
        use_case="codex_usage_reset",
        instruction="Classify whether usage limits recovered.",
        labels=["notify", "ignore", "uncertain"],
        items=items,
    )
    assert calls == 1
    assert [item.label for item in first] == ["notify", "notify"]
    assert [item.label for item in second] == ["notify", "notify"]
    async with factory() as session:
        assert await session.scalar(select(func.count(AIResponseCache.id))) == 2
        invocations = list(
            await session.scalars(select(AIInvocation).order_by(AIInvocation.created_at))
        )
    assert [item.cache_hit for item in invocations] == [False, True]
    assert all(len(item.input_hash) == 64 for item in invocations)
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_gateway_accepts_complete_fenced_json(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        successful = _success_response(request)
        body = json.loads(successful.content)
        content = body["output"]["text"]
        body["output"]["text"] = f"```json\n{content}\n```"
        return httpx.Response(200, json=body)

    service, _factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    result = await service.classify(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-1",
        use_case="test",
        content="candidate",
        instruction="Classify candidate.",
        labels=["notify", "ignore"],
    )
    assert result.label == "notify"
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_gateway_leaves_budget_enforcement_to_the_center(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls > 1:
            return httpx.Response(
                429, json={"error": {"code": "budget_exceeded", "retryable": False}}
            )
        return _success_response(request)

    service, _factory, client = await _configured_service(
        tmp_path, httpx.MockTransport(handler), daily_request_limit=1
    )
    common = dict(
        profile="test_classifier",
        plugin_id=None,
        plugin_run_id=None,
        use_case="budget",
        instruction="Classify.",
        labels=["notify", "ignore"],
    )
    await service.classify(content="first", **common)
    with pytest.raises(AIGatewayError) as error:
        await service.classify(content="second", **common)
    assert error.value.code == "ai_hub_budget_exceeded" and not error.value.retryable
    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_gateway_extracts_summarizes_and_caches(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        user_data = json.loads(payload["messages"][1]["content"])
        options = user_data["options"]
        if "fields" in options:
            calls.append("extract")
            extraction_schema = payload["output"]["schema"]
            assert "reason" not in extraction_schema["properties"]
            assert "reason" not in extraction_schema["required"]
            content = {
                "values": {"version": "0.6.0", "stable": True},
                "confidence": 0.98,
            }
        else:
            calls.append("summarize")
            content = {"summary": "Notify Hub now has an AI Gateway.", "key_points": []}
        return httpx.Response(
            200,
            json={
                "output": {"text": json.dumps(content)},
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    async with factory() as session, session.begin():
        profile = await session.get(AIProfile, "test_classifier")
        assert profile is not None
        profile.capability = "extract"
        profile.include_reason = False
    extraction = await service.extract(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-1",
        use_case="release_fields",
        content="Notify Hub 0.6.0 is stable.",
        instruction="Extract the release fields.",
        fields=["version", "stable"],
    )
    cached_extraction = await service.extract(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-2",
        use_case="release_fields",
        content="Notify Hub 0.6.0 is stable.",
        instruction="Extract the release fields.",
        fields=["version", "stable"],
    )
    async with factory() as session, session.begin():
        profile = await session.get(AIProfile, "test_classifier")
        assert profile is not None
        profile.capability = "summarize"
        profile.revision += 1
    summary = await service.summarize(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-3",
        use_case="release_summary",
        content="Notify Hub now has a provider-neutral AI Gateway.",
        instruction="Summarize the release.",
        max_characters=200,
    )
    assert extraction.values == {"version": "0.6.0", "stable": True}
    assert extraction.reason == ""
    assert cached_extraction == extraction
    assert summary.summary.startswith("Notify Hub")
    assert calls == ["extract", "summarize"]
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_ai_profile_policy_is_applied_and_capability_is_enforced(tmp_path: Path) -> None:
    system_prompts: list[str] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload["parameters"] == {}
        assert "max_tokens" not in payload
        system_prompts.append(payload["messages"][0]["content"])
        schema = payload["output"]["schema"]
        result_schema = schema["properties"]["results"]["items"]
        assert "reason" not in result_schema["properties"]
        assert "reason" not in result_schema["required"]
        response = _success_response(request)
        body = json.loads(response.content)
        if calls == 1:
            content = json.loads(body["output"]["text"])
            for item in content["results"]:
                item.pop("reason")
            body["output"]["text"] = json.dumps(content)
        return httpx.Response(200, json=body)

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    async with factory() as session, session.begin():
        profile = await session.get(AIProfile, "test_classifier")
        assert profile is not None
        profile.output_language = "zh-CN"
        profile.reasoning_effort = "low"
        profile.verbosity = "concise"
        profile.include_reason = False
        profile.max_reason_characters = 0
        profile.system_instructions = "Prefer conservative classifications."
    result = await service.classify(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-1",
        use_case="profile_policy",
        content="candidate",
        instruction="Classify candidate.",
        labels=["notify", "ignore"],
    )
    assert result.reason == ""
    unexpected_reason = await service.classify(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-2",
        use_case="profile_policy",
        content="another candidate",
        instruction="Classify candidate.",
        labels=["notify", "ignore"],
    )
    assert unexpected_reason.reason == ""
    assert "Simplified Chinese" in system_prompts[0]
    assert "Omit the reason field entirely" in system_prompts[0]
    assert "Prefer conservative classifications." in system_prompts[0]
    assert "cannot override platform safety" in system_prompts[0]

    with pytest.raises(AIGatewayError) as exc_info:
        await service.summarize(
            profile="test_classifier",
            plugin_id="test_plugin",
            plugin_run_id="run-3",
            use_case="wrong_capability",
            content="candidate",
            instruction="Summarize candidate.",
        )
    assert exc_info.value.code == "ai_profile_capability_mismatch"
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.parametrize("mode", ["json_object", "prompt_json"])
@pytest.mark.asyncio
async def test_disabled_reason_is_omitted_from_classification_fallback_prompts(
    tmp_path: Path, mode: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system_prompt = payload["messages"][0]["content"]
        assert (
            'Return JSON only with shape {"results":[{"id":string,"label":string,'
            '"confidence":number}]}'
        ) in system_prompt
        assert '"confidence":number,"reason":string' not in system_prompt
        if mode == "json_object":
            assert payload["output"] == {"type": "json"}
        else:
            assert payload["output"] == {"type": "text"}
        response = _success_response(request)
        body = json.loads(response.content)
        content = json.loads(body["output"]["text"])
        for item in content["results"]:
            item.pop("reason")
        body["output"]["text"] = json.dumps(content)
        return httpx.Response(200, json=body)

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    async with factory() as session, session.begin():
        profile = await session.get(AIProfile, "test_classifier")
        assert profile is not None
        profile.include_reason = False
        profile.response_format = mode
    result = await service.classify(
        profile="test_classifier",
        plugin_id="test_plugin",
        plugin_run_id="run-1",
        use_case="profile_policy",
        content="candidate",
        instruction="Classify candidate.",
        labels=["notify", "ignore"],
    )
    assert result.reason == ""
    await client.aclose()
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_center_profile_discovery_revision_and_removal_preserve_local_history(
    tmp_path: Path,
) -> None:
    service, factory, initial_client = await _configured_service(
        tmp_path, httpx.MockTransport(_success_response)
    )
    async with factory() as db:
        connection = await db.get(AIProvider, HUB_CONNECTION_ID)
    first = (await service._hub_client.list_profiles(connection, application_key="test"))[
        0
    ].model_dump()
    first.update(id="center_new", name="New center classifier", revision="a" * 64)
    catalog = [first]
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path.endswith("/profiles"):
            return httpx.Response(200, json={"data": catalog})
        posts += 1
        payload = json.loads(request.content)
        assert payload["parameters"] == {}
        assert payload["task"] == "center_new"
        items = json.loads(payload["messages"][1]["content"])["items"]
        results = [
            {"id": item["id"], "label": "notify", "confidence": 0.95, "reason": "match"}
            for item in items
        ]
        return httpx.Response(
            200,
            json={
                "request_id": "hub-discovery",
                "model": "center-selected-model",
                "output": {
                    "type": "json_schema",
                    "text": json.dumps({"results": results}),
                    "data": {"results": results},
                },
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service._hub_client._client = client
    options = dict(
        profile="center_new",
        plugin_id=None,
        plugin_run_id=None,
        use_case="discovery",
        content="synthetic",
        instruction="Classify.",
        labels=["notify", "ignore"],
    )
    await service.classify(**options)
    await service.classify(**options)
    assert posts == 1
    first["revision"] = "b" * 64
    first["parameters"]["max_output_tokens"] = 256
    await service.classify(**options)
    assert posts == 2
    first.update(enabled=False, revision="c" * 64)
    with pytest.raises(AIGatewayError) as error:
        await service.classify(**options)
    assert error.value.code == "ai_profile_disabled"
    catalog.clear()
    await service.refresh_profiles()
    async with factory() as db:
        profile = await db.get(AIProfile, "center_new")
        assert profile is not None and not profile.enabled and profile.hub_revision is None
        assert (
            await db.scalar(
                select(func.count(AIInvocation.id)).where(AIInvocation.profile_id == "center_new")
            )
            == 3
        )
    assert posts == 2
    await client.aclose()
    await initial_client.aclose()
    await factory.kw["bind"].dispose()
