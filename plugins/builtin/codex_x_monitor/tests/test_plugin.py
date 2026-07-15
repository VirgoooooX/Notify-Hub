from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from twscrape.accounts_pool import NoAccountError

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
    TwscrapeSource,
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


@dataclass
class FakeAIDecision:
    id: str
    label: str = "notify"
    confidence: float = 0.95
    reason: str = "matched"


class FakeAI:
    def __init__(self, *, label: str = "notify", error: Exception | None = None) -> None:
        self.label = label
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def classify_many(self, **kwargs: Any) -> list[FakeAIDecision]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return [FakeAIDecision(id=item.id, label=self.label) for item in kwargs["items"]]


class FakePostSource:
    def __init__(self, posts: list[XPost]) -> None:
        self.posts = posts

    async def fetch(self, _context: Any, _config: Any) -> list[XPost]:
        return self.posts


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
        ai: FakeAI | None = None,
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
        self.ai = ai or FakeAI(error=AssertionError("unexpected AI call"))

    async def get_config(self) -> Mapping[str, Any]:
        return self.config

    async def get_state(self, key: str, default: Any = None) -> Any:
        return self.states.get(key, default)

    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int:
        self.states[key] = value
        self.saved.append(value)
        return len(self.saved)

    async def get_secret(self, name: str) -> str:
        if name == "x_api_bearer_token":
            return self.secret
        if name == "twscrape_cookie":
            if self.secret != "test-bearer-secret":
                return self.secret
            return "auth_token=test-auth-token; ct0=test-ct0"
        raise ValueError(f"unknown secret {name}")

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


def _post(post_id: int, *, reply: bool = False, repost: bool = False) -> XPost:
    return XPost.model_validate(
        {
            "id": str(post_id),
            "author_username": "thsottiaux",
            "text": "Codex weekly quota has been reset and restored",
            "url": f"https://x.com/thsottiaux/status/{post_id}",
            "published_at": f"2026-07-13T10:00:{post_id:02d}Z",
            "is_reply": reply,
            "is_repost": repost,
        }
    )


def test_ai_mode_batches_five_incremental_original_posts_once() -> None:
    ai = FakeAI()
    posts = [_post(index) for index in range(1, 6)]
    context = FakeContext(
        {**BASE_CONFIG, "decision_mode": "rules_then_ai"},
        [],
        state={"last_seen_post_id": "0"},
        ai=ai,
    )
    result = asyncio.run(CodexXMonitorPlugin({"rss": FakePostSource(posts)}).run(context))
    assert len(ai.calls) == 1
    assert len(ai.calls[0]["items"]) == 5
    assert result.emitted_events == 5
    assert context.states[STATE_KEY]["last_seen_post_id"] == "5"


def test_replies_and_reposts_never_reach_ai() -> None:
    ai = FakeAI()
    context = FakeContext(
        {**BASE_CONFIG, "decision_mode": "ai"},
        [],
        state={"last_seen_post_id": "0"},
        ai=ai,
    )
    posts = [_post(1, reply=True), _post(2, repost=True)]
    result = asyncio.run(CodexXMonitorPlugin({"rss": FakePostSource(posts)}).run(context))
    assert ai.calls == []
    assert context.events == []
    assert result.new_posts == 2
    assert context.states[STATE_KEY]["last_seen_post_id"] == "2"


def test_ai_failure_is_fail_closed_without_checkpoint() -> None:
    ai = FakeAI(error=RuntimeError("AI unavailable"))
    context = FakeContext(
        {**BASE_CONFIG, "decision_mode": "rules_then_ai"},
        [],
        state={"last_seen_post_id": "0"},
        ai=ai,
    )
    with pytest.raises(RuntimeError, match="AI unavailable"):
        asyncio.run(CodexXMonitorPlugin({"rss": FakePostSource([_post(1)])}).run(context))
    assert context.saved == []
    assert context.events == []


def test_ai_ignore_checkpoints_without_emitting() -> None:
    ai = FakeAI(label="ignore")
    context = FakeContext(
        {**BASE_CONFIG, "decision_mode": "rules_then_ai"},
        [],
        state={"last_seen_post_id": "0"},
        ai=ai,
    )
    asyncio.run(CodexXMonitorPlugin({"rss": FakePostSource([_post(1)])}).run(context))
    assert context.events == []
    assert context.states[STATE_KEY]["last_seen_post_id"] == "1"


# --- twscrape tests with mock ---


@dataclass
class FakeTweetUser:
    username: str
    displayname: str


@dataclass
class FakeTweet:
    id_str: str
    user: FakeTweetUser
    rawContent: str
    url: str
    date: datetime
    retweetedTweet: Any = None
    inReplyToTweetId: Any = None


def make_fake_tweet(
    id_str: str,
    username: str = "thsottiaux",
    text: str = "Codex resets weekly limits!",
    is_repost: bool = False,
    is_reply: bool = False,
) -> FakeTweet:
    from datetime import UTC, datetime

    return FakeTweet(
        id_str=id_str,
        user=FakeTweetUser(username=username, displayname="Thsottiaux"),
        rawContent=text,
        url=f"https://x.com/{username}/status/{id_str}",
        date=datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC),
        retweetedTweet=FakeTweet(
            "999", FakeTweetUser("other", "Other"), "repost", "url", datetime.now(UTC)
        )
        if is_repost
        else None,
        inReplyToTweetId="888" if is_reply else None,
    )


@patch("plugins.shared.x_monitor.twscrape_source.API")
@patch("plugins.shared.x_monitor.twscrape_source.gather")
def test_twscrape_fetch_with_replies(mock_gather: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api = MagicMock()
    mock_api_cls.return_value = mock_api
    mock_api.user_by_login = AsyncMock(return_value=MagicMock(id="12345"))
    mock_api.user_tweets_and_replies = MagicMock()
    mock_api.pool.add_account_cookies = AsyncMock()

    tweets = [
        make_fake_tweet("1", text="Codex reset replies"),
        make_fake_tweet("2", is_reply=True),
        make_fake_tweet("3", is_repost=True),
        make_fake_tweet("4", username="injected_user"),  # should be filtered out
    ]
    mock_gather.return_value = tweets

    config = CodexXMonitorConfig.model_validate(
        {
            "source": "twscrape",
            "include_replies": True,
            "include_reposts": True,
            "original_posts_only": False,
        }
    )
    context = FakeContext(config.model_dump(), [])

    posts = asyncio.run(TwscrapeSource().fetch(context, config))

    assert len(posts) == 3
    assert posts[0].id == "1"
    assert posts[1].is_reply is True
    assert posts[2].is_repost is True

    mock_api.user_tweets_and_replies.assert_called_once_with("12345", limit=40)
    mock_api.pool.add_account_cookies.assert_called_once()


@patch("plugins.shared.x_monitor.twscrape_source.API")
@patch("plugins.shared.x_monitor.twscrape_source.gather")
def test_twscrape_fetch_without_replies(mock_gather: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api = MagicMock()
    mock_api_cls.return_value = mock_api
    mock_api.user_by_login = AsyncMock(return_value=MagicMock(id="12345"))
    mock_api.user_tweets = MagicMock()
    mock_api.pool.add_account_cookies = AsyncMock()

    mock_gather.return_value = [make_fake_tweet("1")]

    config = CodexXMonitorConfig.model_validate(
        {
            "source": "twscrape",
            "include_replies": False,
        }
    )
    context = FakeContext(config.model_dump(), [])

    posts = asyncio.run(TwscrapeSource().fetch(context, config))

    assert len(posts) == 1
    mock_api.user_tweets.assert_called_once_with("12345", limit=40)


def test_twscrape_cookie_validation() -> None:
    config = CodexXMonitorConfig.model_validate({"source": "twscrape"})

    # Missing auth_token
    context_no_auth = FakeContext(config.model_dump(), [], secret="ct0=abc")
    context_no_auth.secret = "ct0=abc"  # Override
    with pytest.raises(SourceError, match="does not contain auth_token"):
        asyncio.run(TwscrapeSource().fetch(context_no_auth, config))

    # Missing ct0
    context_no_ct0 = FakeContext(config.model_dump(), [], secret="auth_token=abc")
    context_no_ct0.secret = "auth_token=abc"  # Override
    with pytest.raises(SourceError, match="does not contain ct0"):
        asyncio.run(TwscrapeSource().fetch(context_no_ct0, config))


@patch("plugins.shared.x_monitor.twscrape_source.API")
def test_twscrape_no_account_error_mapping(mock_api_cls: MagicMock) -> None:
    mock_api = MagicMock()
    mock_api_cls.return_value = mock_api
    mock_api.pool.add_account_cookies = AsyncMock()
    mock_api.user_by_login = AsyncMock(side_effect=NoAccountError("No account"))

    config = CodexXMonitorConfig.model_validate({"source": "twscrape"})
    context = FakeContext(config.model_dump(), [])

    with pytest.raises(SourceRateLimited, match="source rate limited"):
        asyncio.run(TwscrapeSource().fetch(context, config))


@patch("plugins.shared.x_monitor.twscrape_source.API")
def test_twscrape_general_exception_masking(mock_api_cls: MagicMock) -> None:
    mock_api = MagicMock()
    mock_api_cls.return_value = mock_api
    mock_api.pool.add_account_cookies = AsyncMock()
    mock_api.user_by_login = AsyncMock(
        side_effect=RuntimeError("Some X details with cookie values")
    )

    config = CodexXMonitorConfig.model_validate({"source": "twscrape"})
    context = FakeContext(config.model_dump(), [])

    with pytest.raises(SourceError) as caught:
        asyncio.run(TwscrapeSource().fetch(context, config))

    # Verify the original exception message that might contain cookie details is masked
    assert "Some X details" not in str(caught.value)
    assert "twscrape request failed" in str(caught.value)


@patch("plugins.shared.x_monitor.twscrape_source.API")
@patch("plugins.shared.x_monitor.twscrape_source.gather")
def test_twscrape_temp_dir_deleted(mock_gather: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api = MagicMock()
    mock_api_cls.return_value = mock_api
    mock_api.user_by_login = AsyncMock(return_value=MagicMock(id="12345"))
    mock_api.user_tweets = MagicMock()
    mock_api.pool.add_account_cookies = AsyncMock()
    mock_gather.return_value = []

    config = CodexXMonitorConfig.model_validate({"source": "twscrape"})
    context = FakeContext(config.model_dump(), [])

    # We will patch Path.exists to check temporary accounts.db deletion behavior
    with patch("plugins.shared.x_monitor.twscrape_source.TemporaryDirectory") as mock_temp_dir:
        mock_temp_dir.return_value.__enter__.return_value = "/mock/temp/dir"

        asyncio.run(TwscrapeSource().fetch(context, config))

        # Verify the context manager cleanup was called
        mock_temp_dir.return_value.__exit__.assert_called_once()
