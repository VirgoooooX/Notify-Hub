from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.ai.provider import AIProviderError, OpenAICompatibleClient
from app.ai.schemas import AIClassificationItem
from app.ai.service import AIGatewayError, AIService
from app.application.ai_control_service import AIControlService
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
    await control.create_provider(
        {
            "id": "aip_test",
            "name": "Test",
            "preset": "custom",
            "protocol": "openai_chat_completions",
            "base_url": "https://provider.example.test/v1",
            "enabled": True,
            "allow_private_network": False,
            "timeout_seconds": 5,
            "max_retries": 0,
            "verify_tls": True,
            "structured_output_mode": "auto",
            "custom_query": {},
        }
    )
    await control.sync_provider_models("aip_test", ["test-model"])
    await control.set_allowed_models("aip_test", ["test-model"])
    await control.create_profile(
        {
            "id": "test_classifier",
            "name": "Test classifier",
            "provider_id": "aip_test",
            "model": "test-model",
            "temperature": 0,
            "max_output_tokens": 160,
            "response_format": "auto",
            "timeout_seconds": 5,
            "cache_ttl_seconds": 3600,
            "daily_request_limit": daily_request_limit,
            "daily_token_limit": None,
            "enabled": True,
        }
    )
    client = httpx.AsyncClient(transport=handler)
    provider_client = OpenAICompatibleClient(resolver=_public_resolver, client=client)
    return AIService(factory, provider_client=provider_client), factory, client


def _success_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    user_data = json.loads(payload["messages"][1]["content"])
    results = [
        {"id": item["id"], "label": "notify", "confidence": 0.95, "reason": "match"}
        for item in user_data["items"]
    ]
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps({"results": results})}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
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
async def test_ai_gateway_falls_back_from_json_schema(tmp_path: Path) -> None:
    modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        mode = payload.get("response_format", {}).get("type", "prompt_json")
        modes.append(mode)
        if mode == "json_schema":
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return _success_response(request)

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
    assert modes == ["json_schema", "json_object"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_gateway_enforces_daily_request_budget(tmp_path: Path) -> None:
    service, _factory, client = await _configured_service(
        tmp_path, httpx.MockTransport(_success_response), daily_request_limit=1
    )
    common = {
        "profile": "test_classifier",
        "plugin_id": "test_plugin",
        "plugin_run_id": "run-1",
        "use_case": "test",
        "instruction": "Classify candidate.",
        "labels": ["notify", "ignore"],
    }
    await service.classify(content="first", **common)
    with pytest.raises(AIGatewayError, match="daily request limit") as exc_info:
        await service.classify(content="second", **common)
    assert exc_info.value.code == "ai_budget_exceeded"
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
            content = {
                "values": {"version": "0.6.0", "stable": True},
                "confidence": 0.98,
                "reason": "explicit release text",
            }
        else:
            calls.append("summarize")
            content = {"summary": "Notify Hub now has an AI Gateway.", "key_points": []}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    service, factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    async with factory() as session, session.begin():
        profile = await session.get(AIProfile, "test_classifier")
        assert profile is not None
        profile.capability = "extract"
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
    assert cached_extraction == extraction
    assert summary.summary.startswith("Notify Hub")
    assert calls == ["extract", "summarize"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_profile_policy_is_applied_and_capability_is_enforced(tmp_path: Path) -> None:
    system_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system_prompts.append(payload["messages"][0]["content"])
        return _success_response(request)

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
    assert "Simplified Chinese" in system_prompts[0]
    assert "Prefer conservative classifications." in system_prompts[0]
    assert "cannot override platform safety" in system_prompts[0]

    with pytest.raises(AIGatewayError) as exc_info:
        await service.summarize(
            profile="test_classifier",
            plugin_id="test_plugin",
            plugin_run_id="run-2",
            use_case="wrong_capability",
            content="candidate",
            instruction="Summarize candidate.",
        )
    assert exc_info.value.code == "ai_profile_capability_mismatch"
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_provider_lists_models_without_exposing_response_body(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})

    service, _factory, client = await _configured_service(tmp_path, httpx.MockTransport(handler))
    assert await service.list_models("aip_test") == ["model-a", "model-b"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ai_provider_blocks_private_and_metadata_addresses() -> None:
    provider = AIProvider(
        id="aip_unsafe",
        name="Unsafe",
        preset="custom",
        protocol="openai_chat_completions",
        base_url="https://provider.example.test/v1",
        enabled=True,
        allow_private_network=False,
        timeout_seconds=5,
        max_retries=0,
        verify_tls=True,
        structured_output_mode="auto",
        custom_query={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def private_resolver(_host: str, _port: int) -> set[ipaddress.IPv4Address]:
        return {ipaddress.ip_address("127.0.0.1")}

    client = OpenAICompatibleClient(resolver=private_resolver)
    with pytest.raises(AIProviderError) as exc_info:
        await client.complete(
            provider,
            api_key="must-not-appear",
            model="test",
            messages=[{"role": "user", "content": "test"}],
            temperature=0,
            max_output_tokens=10,
            response_format=None,
            timeout_seconds=5,
        )
    assert exc_info.value.code == "ai_unsafe_url"
    assert "must-not-appear" not in str(exc_info.value)
