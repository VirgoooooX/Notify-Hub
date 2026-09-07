from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.application.x_source_service import (
    RssHubProvider,
    TwscrapeProvider,
    XSourceFetchResult,
    XSourceService,
    parse_rsshub_feed,
)
from app.config import Settings
from app.domain.x_source import (
    XSourceAccountError,
    XSourceCookieInvalid,
    XSourceParseError,
    XSourcesUnavailable,
    XSourceUnavailable,
)

from plugins.shared.x_monitor.rsshub_source import RssHubTimelineSource

FIXTURES = Path(__file__).parent / "fixtures"


def test_rsshub_parser_preserves_stable_id_text_and_cover() -> None:
    posts = parse_rsshub_feed(
        (FIXTURES / "rsshub_timeline.xml").read_text(encoding="utf-8"),
        "FabrizioRomano",
    )

    assert [post.id for post in posts] == ["2001", "2002"]
    assert posts[-1].text == "Joao Pedro to Chelsea, HERE WE GO! 🚨"
    assert str(posts[-1].photo_urls[0]).startswith("https://pbs.twimg.com/media/cover.jpg")
    assert posts[-1].author_username == "FabrizioRomano"


def test_rsshub_parser_rejects_malformed_xml() -> None:
    with pytest.raises(XSourceParseError, match="invalid RSS/Atom XML"):
        parse_rsshub_feed("<rss><broken>", "thsottiaux")


@pytest.mark.asyncio
async def test_rsshub_provider_encodes_username_and_limits_posts() -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200,
            text=(FIXTURES / "rsshub_timeline.xml").read_text(encoding="utf-8"),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RssHubProvider("http://rsshub:1200", client=client)
    try:
        posts = await provider.fetch("user name", limit=1, include_replies=False)
    finally:
        await provider.close()

    assert len(posts) == 1
    assert posts[0].id == "2002"
    assert "/twitter/user/user%20name" in requested[0]


@pytest.mark.asyncio
async def test_rsshub_provider_classifies_account_not_found() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RssHubProvider("http://rsshub:1200", client=client)
    try:
        with pytest.raises(XSourceAccountError):
            await provider.fetch("missing", limit=40, include_replies=False)
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_rsshub_provider_classifies_timeout_as_source_unavailable() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RssHubProvider("http://rsshub:1200", client=client)
    try:
        with pytest.raises(XSourceUnavailable, match="timed out"):
            await provider.fetch("thsottiaux", limit=40, include_replies=False)
    finally:
        await provider.close()


def test_settings_default_to_rsshub_without_twscrape_cookie() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
    )
    assert settings.x_source_provider == "rsshub"
    assert settings.x_twscrape_cookie is None


@pytest.mark.parametrize("cookie", ["", "ct0=test-only", "auth_token=test-only"])
def test_settings_reject_incomplete_twscrape_cold_standby_cookie(cookie: str) -> None:
    with pytest.raises(ValueError, match=r"auth_token and ct0|required"):
        Settings(
            _env_file=None,
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            jwt_secret="test-secret-that-is-long-enough-for-jwt",
            x_source_provider="twscrape",
            x_twscrape_cookie=cookie,
        )


@pytest.mark.asyncio
async def test_platform_twscrape_provider_uses_platform_cookie_without_logging_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = MagicMock()
    api.pool.add_account_cookies = AsyncMock()
    api.user_by_login = AsyncMock(return_value=SimpleNamespace(id="123"))
    api.user_tweets = MagicMock(return_value=[])
    monkeypatch.setattr("twscrape.API", MagicMock(return_value=api))
    monkeypatch.setattr("twscrape.gather", AsyncMock(return_value=[]))

    cookie = "auth_token=test-only; ct0=test-only"
    provider = TwscrapeProvider(cookie)
    assert await provider.fetch("thsottiaux", limit=10, include_replies=False) == []
    api.pool.add_account_cookies.assert_awaited_once_with("notify-hub", cookie)


@pytest.mark.asyncio
async def test_twscrape_provider_deduplicates_filters_replies_and_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    user = SimpleNamespace(username="thsottiaux", displayname="Thsottiaux")

    def tweet(post_id: str, *, seconds: int, reply: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            id_str=post_id,
            user=user,
            rawContent=f"post {post_id}",
            url=f"https://x.com/thsottiaux/status/{post_id}",
            date=datetime(2026, 8, 25, 8, 0, seconds, tzinfo=UTC),
            retweetedTweet=None,
            inReplyToTweetId="reply" if reply else None,
            media=None,
            quotedTweet=None,
        )

    api = MagicMock()
    api.pool.add_account_cookies = AsyncMock()
    api.user_by_login = AsyncMock(return_value=SimpleNamespace(id="123"))
    api.user_tweets = MagicMock()
    monkeypatch.setattr("twscrape.API", MagicMock(return_value=api))
    monkeypatch.setattr(
        "twscrape.gather",
        AsyncMock(
            return_value=[
                tweet("3", seconds=3),
                tweet("1", seconds=1),
                tweet("1", seconds=1),
                tweet("2", seconds=2, reply=True),
            ]
        ),
    )

    posts = await TwscrapeProvider("auth_token=test; ct0=test").fetch(
        "thsottiaux", limit=2, include_replies=False
    )

    assert [post.id for post in posts] == ["1", "3"]
    api.user_tweets.assert_called_once_with("123", limit=2)


@pytest.mark.asyncio
async def test_twscrape_primary_falls_back_to_rsshub_once() -> None:
    class FakeProvider:
        def __init__(self, value: object) -> None:
            self.value = value
            self.calls = 0

        async def fetch(self, *_args: object, **_kwargs: object) -> list[object]:
            self.calls += 1
            if isinstance(self.value, Exception):
                raise self.value
            return self.value  # type: ignore[return-value]

        async def close(self) -> None:
            return None

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_source_provider="twscrape",
        x_twscrape_cookie="auth_token=test; ct0=test",
    )
    service = XSourceService(settings)
    primary = FakeProvider(XSourceCookieInvalid("Cookie rejected"))
    fallback = FakeProvider([])
    service._twscrape = primary  # type: ignore[assignment]
    service._rsshub = fallback  # type: ignore[assignment]
    try:
        result = await service.fetch_with_status("thsottiaux", limit=40)
    finally:
        await service.close()

    assert isinstance(result, XSourceFetchResult)
    assert result.provider_used == "rsshub"
    assert result.degraded_error is not None
    assert result.degraded_error.code == "x_source_cookie_invalid"
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_twscrape_and_rsshub_failure_is_typed() -> None:
    class FailingProvider:
        def __init__(self, error: Exception) -> None:
            self.error = error

        async def fetch(self, *_args: object, **_kwargs: object) -> list[object]:
            raise self.error

        async def close(self) -> None:
            return None

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_source_provider="twscrape",
        x_twscrape_cookie="auth_token=test; ct0=test",
    )
    service = XSourceService(settings)
    service._twscrape = FailingProvider(XSourceCookieInvalid("Cookie rejected"))  # type: ignore[assignment]
    service._rsshub = FailingProvider(XSourceUnavailable("RSSHub down"))  # type: ignore[assignment]
    try:
        with pytest.raises(XSourcesUnavailable) as caught:
            await service.fetch_with_status("thsottiaux")
    finally:
        await service.close()

    assert caught.value.code == "x_sources_unavailable"
    assert caught.value.primary_code == "x_source_cookie_invalid"
    assert caught.value.fallback_code == "x_source_unavailable"


@pytest.mark.asyncio
async def test_plugin_adapter_reads_platform_timeline_capability() -> None:
    calls: list[tuple[str, int, bool]] = []

    class PlatformX:
        async def timeline(
            self, username: str, *, limit: int, include_replies: bool
        ) -> list[dict[str, object]]:
            calls.append((username, limit, include_replies))
            return [
                {
                    "id": "2002",
                    "author_username": username,
                    "text": "HERE WE GO",
                    "url": f"https://x.com/{username}/status/2002",
                    "published_at": "2026-08-25T08:00:00Z",
                }
            ]

    posts = await RssHubTimelineSource().fetch(
        SimpleNamespace(x=PlatformX()),
        "FabrizioRomano",
        20,
        False,
    )

    assert [post.id for post in posts] == ["2002"]
    assert calls == [("FabrizioRomano", 20, False)]
