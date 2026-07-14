from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from plugins.builtin.codex_x_monitor.matcher import match_post
from plugins.builtin.codex_x_monitor.plugin import CodexXMonitorPlugin
from plugins.builtin.codex_x_monitor.schemas import (
    STATE_KEY,
    CodexXMonitorConfig,
    EventDraft,
    RestrictedHttpClient,
    XPost,
)
from plugins.builtin.codex_x_monitor.sources import (
    HTTP_TIMEOUT_SECONDS,
    SourceError,
    SourceParseError,
    SourceRateLimited,
    XApiSource,
    parse_feed,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASE_CONFIG = {
    "source": "rss",
    "feed_url": "https://rss.example.com/thsottiaux/rss",
}


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    payload: Any = None

    def json(self) -> Any:
        return self.payload


class FakeHttp:
    def __init__(
        self,
        responses: list[FakeResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,  # noqa: ASYNC109 -- mirrors the production HTTP Protocol.
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.error:
            raise self.error
        return self.responses.pop(0)


class FakeContext:
    def __init__(
        self,
        config: Mapping[str, Any],
        responses: list[FakeResponse],
        *,
        state: Mapping[str, Any] | None = None,
        receipts: list[Any] | None = None,
        emit_error: Exception | None = None,
        secret: str = "test-bearer-secret",
    ) -> None:
        self.config = dict(config)
        self.fake_http = FakeHttp(responses)
        self.http: RestrictedHttpClient = self.fake_http
        self.states: dict[str, Any] = {STATE_KEY: dict(state)} if state else {}
        self.receipts = list(receipts or [])
        self.emit_error = emit_error
        self.secret = secret
        self.events: list[EventDraft] = []
        self.saved: list[Any] = []

    async def get_config(self) -> Mapping[str, Any]:
        return self.config

    async def get_state(self, key: str, default: Any = None) -> Any:
        return self.states.get(key, default)

    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int:
        self.states[key] = value
        self.saved.append(value)
        return len(self.saved)

    async def get_secret(self, name: str) -> str:
        assert name == "x_api_bearer_token"
        return self.secret

    async def emit_event(self, event: EventDraft) -> Any:
        self.events.append(event)
        if self.emit_error:
            raise self.emit_error
        return self.receipts.pop(0) if self.receipts else {"status": "accepted"}


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_config_validation_and_schema() -> None:
    with pytest.raises(ValidationError):
        CodexXMonitorConfig.model_validate({"source": "rss", "interval_seconds": 59})
    with pytest.raises(ValidationError):
        CodexXMonitorConfig.model_validate({"source": "rss"})
    with pytest.raises(ValidationError):
        CodexXMonitorConfig.model_validate({**BASE_CONFIG, "match_mode": "rules_then_llm"})
    schema = CodexXMonitorPlugin.config_schema()
    assert "x_api_bearer_token" not in json.dumps(schema)


def test_parser_handles_atom_duplicates_and_missing_stable_ids() -> None:
    posts = parse_feed(fixture_text("duplicate_and_missing_id.xml"), "thsottiaux")
    assert [post.id for post in posts] == ["2061106703446450396"]


def test_parser_rejects_malformed_xml() -> None:
    with pytest.raises(SourceParseError, match="invalid RSS/Atom XML"):
        parse_feed("<rss><broken>", "thsottiaux")


def test_default_first_run_baselines_without_emitting() -> None:
    context = FakeContext(BASE_CONFIG, [FakeResponse(text=fixture_text("baseline.xml"))])
    result = asyncio.run(CodexXMonitorPlugin().run(context))
    assert result.status == "baseline_initialized"
    assert context.events == []
    assert context.states[STATE_KEY]["last_seen_post_id"] == "2061106703446450392"
    assert context.fake_http.calls[0]["timeout"] == HTTP_TIMEOUT_SECONDS


def test_scan_recent_processes_history_and_uses_stable_event_key() -> None:
    context = FakeContext(
        {**BASE_CONFIG, "first_run_mode": "scan_recent", "scan_recent_limit": 1},
        [FakeResponse(text=fixture_text("baseline.xml"))],
    )
    result = asyncio.run(CodexXMonitorPlugin().run(context))
    assert result.emitted_events == 1
    assert [event.event_key for event in context.events] == ["x-post-2061106703446450392"]
    assert context.events[0].article is not None


def test_posts_are_processed_in_order_and_negative_match_is_excluded() -> None:
    context = FakeContext(
        BASE_CONFIG,
        [FakeResponse(text=fixture_text("new_posts_out_of_order.xml"))],
        state={"last_seen_post_id": "2061106703446450392"},
    )
    result = asyncio.run(CodexXMonitorPlugin().run(context))
    assert result.new_posts == 3
    assert [event.event_key for event in context.events] == ["x-post-2061106703446450395"]
    assert [saved["last_seen_post_id"] for saved in context.saved[:3]] == [
        "2061106703446450393",
        "2061106703446450394",
        "2061106703446450395",
    ]


def test_emit_failure_does_not_advance_over_failed_post() -> None:
    context = FakeContext(
        BASE_CONFIG,
        [FakeResponse(text=fixture_text("new_posts_out_of_order.xml"))],
        state={"last_seen_post_id": "2061106703446450392"},
        emit_error=RuntimeError("core unavailable"),
    )
    with pytest.raises(RuntimeError, match="core unavailable"):
        asyncio.run(CodexXMonitorPlugin().run(context))
    assert context.states[STATE_KEY]["last_seen_post_id"] == "2061106703446450394"
    assert all(saved["last_seen_post_id"] != "2061106703446450395" for saved in context.saved)


def test_duplicate_receipt_advances_cursor_but_is_not_counted_as_emitted() -> None:
    context = FakeContext(
        BASE_CONFIG,
        [FakeResponse(text=fixture_text("duplicate_and_missing_id.xml"))],
        state={"last_seen_post_id": "2061106703446450395"},
        receipts=[{"status": "duplicate"}],
    )
    result = asyncio.run(CodexXMonitorPlugin().run(context))
    assert result.emitted_events == 0
    assert context.states[STATE_KEY]["last_seen_post_id"] == "2061106703446450396"


def test_source_switch_keeps_cursor_and_event_key() -> None:
    x_payload = json.loads(fixture_text("x_api_posts.json"))
    context = FakeContext(
        {"source": "x_api"},
        [
            FakeResponse(payload={"data": {"id": "42"}}),
            FakeResponse(payload=x_payload),
        ],
        state={"last_seen_post_id": "2061106703446450395", "last_source": "rss"},
    )
    asyncio.run(CodexXMonitorPlugin().run(context))
    assert context.events[0].event_key == "x-post-2061106703446450396"
    assert context.states[STATE_KEY]["last_source"] == "x_api"
    assert context.secret not in repr(context.events)
    assert context.secret not in repr(context.states)


def test_x_api_error_does_not_expose_bearer_token() -> None:
    context = FakeContext(
        {"source": "x_api"},
        [FakeResponse(status_code=401)],
        secret="must-never-appear",
    )
    with pytest.raises(SourceError) as caught:
        config = CodexXMonitorConfig.model_validate({"source": "x_api"})
        asyncio.run(XApiSource().fetch(context, config))
    assert context.secret not in str(caught.value)


def test_x_api_rate_limit_is_typed_and_does_not_save_state() -> None:
    context = FakeContext(
        {"source": "x_api"},
        [FakeResponse(status_code=429, headers={"x-rate-limit-reset": "12345"})],
        state={"last_seen_post_id": "2061106703446450395"},
    )
    with pytest.raises(SourceRateLimited, match="12345"):
        asyncio.run(CodexXMonitorPlugin().run(context))
    assert context.saved == []


def test_matcher_filters_reposts_replies_and_negative_semantics() -> None:
    config = CodexXMonitorConfig.model_validate(BASE_CONFIG)
    post = XPost.model_validate(
        {
            "id": "1",
            "author_username": "thsottiaux",
            "text": "@someone Codex quota was NOT reset https://example.com/a",
            "url": "https://x.com/thsottiaux/status/1",
            "published_at": "2026-07-13T10:00:00Z",
        }
    )
    result = match_post(post, config)
    assert result.matched is False
    assert result.excluded_by


def test_source_timeout_propagates_without_cursor_change() -> None:
    context = FakeContext(BASE_CONFIG, [], state={"last_seen_post_id": "1"})
    context.fake_http.error = TimeoutError("source timed out")
    with pytest.raises(TimeoutError, match="source timed out"):
        asyncio.run(CodexXMonitorPlugin().run(context))
    assert context.saved == []
