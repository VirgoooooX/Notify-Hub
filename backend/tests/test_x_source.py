from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.application.x_source_service import (
    RssHubProvider,
    TwscrapeProvider,
    parse_rsshub_feed,
)
from app.config import Settings
from app.domain.x_source import (
    XSourceAccountError,
    XSourceParseError,
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
