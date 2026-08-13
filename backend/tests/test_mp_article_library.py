from __future__ import annotations

import ipaddress
import struct
import zlib
from typing import Any

import httpx
import pytest
from app.api.errors import AppError
from app.channels.base import ChannelMessage
from app.channels.mp.adapter import MPArticleAdapter
from app.channels.mp.client import MPClient
from app.channels.mp.render import render_body, render_wechat_html
from app.config import Settings
from app.domain.clock import SystemClock
from app.infrastructure.database.models import Delivery, MpArticle
from app.media.downloader import SafeMediaDownloader
from pydantic import SecretStr
from sqlalchemy import select


def make_settings(*, publish_mode: str = "publish", credentials: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        mp_app_id="wx123" if credentials else None,
        mp_app_secret=SecretStr("mp-secret") if credentials else None,
        mp_publish_mode=publish_mode,
        mp_author="Test Author",
    )


def make_transport(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://mp.test")


def _png_bytes() -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + chunk(b"IEND", b"")


def make_downloader(http: httpx.AsyncClient) -> SafeMediaDownloader:
    async def _resolver(
        _host: str, _port: int
    ) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        return {ipaddress.ip_address("93.184.216.34")}

    return SafeMediaDownloader(http, resolver=_resolver)


def article_message(delivery_id: str = "delivery_lib") -> ChannelMessage:
    return ChannelMessage(
        message_type="article",
        title="Codex 用量可能已重置",
        content="# 摘要\n\n第一段\n\n- 要点一\n- 要点二",
        recipients=[],
        url="https://x.com/post/1",
        image_url="https://img.example.com/cover.png",
        payload={
            "publish_to_mp": True,
            "article_digest": "摘要内容",
            "article_ai_profile": "article_writer",
            "article_ai_status": "ai_summarized",
        },
        delivery_id=delivery_id,
    )


class FakeLibrary:
    def __init__(self) -> None:
        self.stored: list[dict[str, Any]] = []
        self.fail = False

    async def store_from_delivery(self, **kwargs: Any) -> str:
        if self.fail:
            raise RuntimeError("boom")
        self.stored.append(kwargs)
        return "mpa_fake_1"


def _api_handler(requests: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/cgi-bin/token"):
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 7200})
        if path.endswith("/cgi-bin/material/add_material"):
            return httpx.Response(200, json={"media_id": "media-1"})
        if path.endswith("/cgi-bin/draft/add"):
            return httpx.Response(200, json={"media_id": "draft-1"})
        if path.endswith("/cgi-bin/freepublish/submit"):
            return httpx.Response(200, json={"publish_id": 123})
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=_png_bytes(),
        )

    return handler


def test_render_body_supports_headings_lists_and_quotes() -> None:
    html = render_body("# 标题\n\n第一段 <b>x</b>\n\n- 一\n- 二\n\n> 引用")
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<ul style=" in html
    assert "<blockquote style=" in html
    assert html.startswith("<p style=")


def test_render_wechat_html_includes_cover_and_source() -> None:
    html = render_wechat_html(
        content="正文",
        cover_url="https://img.example.com/cover.png?x=1&y=2",
        source_url="https://x.com/post/1",
    )
    assert html.startswith("<section style=")
    assert '<img src="https://img.example.com/cover.png?x=1&amp;y=2"' in html
    assert "原文链接：" in html
    assert 'href="https://x.com/post/1"' in html


@pytest.mark.asyncio
async def test_adapter_library_mode_stores_article_without_credentials() -> None:
    library = FakeLibrary()
    adapter = MPArticleAdapter(
        None,
        make_settings(credentials=False),
        library=library,
    )
    result = await adapter.send(article_message())

    assert result.success is True
    assert result.provider_message_id == "mpa_fake_1"
    assert result.response_metadata["publish_mode"] == "library"
    assert result.response_metadata["manual_publish_required"] is True
    assert library.stored[0]["status"] == "ready"
    assert library.stored[0]["delivery_id"] == "delivery_lib"


@pytest.mark.asyncio
async def test_adapter_library_mode_explicit_mode_with_credentials() -> None:
    library = FakeLibrary()
    adapter = MPArticleAdapter(
        MPClient(make_settings(publish_mode="library"), SystemClock()),
        make_settings(publish_mode="library"),
        library=library,
    )
    result = await adapter.send(article_message())
    assert result.success is True
    assert result.response_metadata["publish_mode"] == "library"


@pytest.mark.asyncio
async def test_adapter_library_mode_requires_library() -> None:
    adapter = MPArticleAdapter(None, make_settings(credentials=False))
    result = await adapter.send(article_message())
    assert result.success is False
    assert result.error_code == "CHANNEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_adapter_library_store_failure_is_retryable() -> None:
    library = FakeLibrary()
    library.fail = True
    adapter = MPArticleAdapter(None, make_settings(credentials=False), library=library)
    result = await adapter.send(article_message())
    assert result.success is False
    assert result.retryable is True
    assert result.error_code == "LIBRARY_STORE_FAILED"


@pytest.mark.asyncio
async def test_adapter_api_mode_records_library_article() -> None:
    requests: list[httpx.Request] = []
    library = FakeLibrary()
    settings = make_settings()
    http = make_transport(_api_handler(requests))
    adapter = MPArticleAdapter(
        MPClient(settings, SystemClock(), http_client=http),
        settings,
        downloader=make_downloader(http),
        library=library,
    )
    try:
        result = await adapter.send(article_message())
    finally:
        await http.aclose()

    assert result.success is True
    assert result.response_metadata["publish_mode"] == "publish"
    assert result.response_metadata["article_recorded"] is True
    stored = library.stored[0]
    assert stored["status"] == "published"
    assert stored["provider_publish_id"] == "123"
    assert stored["provider_draft_media_id"] == "draft-1"


@pytest.mark.asyncio
async def test_adapter_test_returns_success_in_library_mode() -> None:
    adapter = MPArticleAdapter(None, make_settings(credentials=False))
    result = await adapter.test("unused")
    assert result.success is True
    assert result.response_metadata["publish_mode"] == "library"


@pytest.mark.integration
async def test_library_delivery_creates_ready_article(api: tuple[Any, Any]) -> None:
    _client, app = api
    await app.state.event_service.accept_internal_event(
        source_type="plugin",
        source_id="codex_x_monitor",
        event_type="codex.usage_reset",
        event_key="x-post-lib-1",
        title="Codex 用量可能已重置",
        content="正文内容",
        recipients=[],
        message_type="article",
        image_url="https://notify.example.com/cover.png",
        url="https://x.com/post/1",
        payload={
            "article_ai_profile": "article_writer",
            "article_ai_status": "ai_summarized",
        },
        publish_to_mp=True,
    )

    assert await app.state.delivery_worker.process_one() is True

    async with app.state.session_factory() as session:
        articles = list(await session.scalars(select(MpArticle)))
        assert len(articles) == 1
        article = articles[0]
        assert article.status == "ready"
        assert article.delivery_id is not None
        assert article.title == "Codex 用量可能已重置"
        assert article.author == "Notify Hub"
        assert article.cover_url == "https://notify.example.com/cover.png"
        assert article.source_url == "https://x.com/post/1"
        assert "<section style=" in article.content_html
        assert article.ai_profile == "article_writer"
        assert article.ai_status == "ai_summarized"
        assert article.event_key == "x-post-lib-1"
        delivery = await session.get(Delivery, article.delivery_id)
        assert delivery is not None
        assert delivery.status == "succeeded"


@pytest.mark.integration
async def test_article_library_service_transitions(api: tuple[Any, Any]) -> None:
    _client, app = api
    library = app.state.mp_article_library
    article_id = await library.store_from_delivery(
        delivery_id=None,
        message=article_message("delivery_transition"),
        status="ready",
    )

    published = await library.mark_published(article_id)
    assert published.status == "published"
    assert published.published_at is not None

    with pytest.raises(AppError) as exc_info:
        await library.mark_ignored(article_id)
    assert exc_info.value.code == "invalid_status_transition"

    restored = await library.restore(article_id)
    assert restored.status == "ready"

    ignored = await library.mark_ignored(article_id)
    assert ignored.status == "ignored"
    with pytest.raises(AppError) as exc_info:
        await library.mark_published(article_id)
    assert exc_info.value.code == "invalid_status_transition"


@pytest.mark.integration
async def test_article_library_store_is_idempotent_per_delivery(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    library = app.state.mp_article_library
    first = await library.store_from_delivery(
        delivery_id="delivery_idem",
        message=article_message("delivery_idem"),
        status="ready",
    )
    second = await library.store_from_delivery(
        delivery_id="delivery_idem",
        message=article_message("delivery_idem"),
        status="ready",
    )
    assert first == second

    async with app.state.session_factory() as session:
        rows = list(await session.scalars(select(MpArticle)))
        assert len(rows) == 1


@pytest.mark.integration
async def test_article_admin_api_round_trip(api: tuple[Any, Any]) -> None:
    client, app = api
    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "administrator", "password": "correct-horse-battery-staple"},
    )
    assert initialized.status_code == 201
    token = initialized.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    article_id = await app.state.mp_article_library.store_from_delivery(
        delivery_id=None,
        message=article_message("delivery_api"),
        status="ready",
    )

    config_response = await client.get("/api/v1/admin/articles/config", headers=headers)
    assert config_response.status_code == 200
    config = config_response.json()["data"]
    assert config["configured"] is False
    assert config["effective_mode"] == "library"

    listed = await client.get("/api/v1/admin/articles?status=ready", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1

    detail = await client.get(f"/api/v1/admin/articles/{article_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["content_html"].startswith("<section style=")

    published = await client.post(f"/api/v1/admin/articles/{article_id}/publish", headers=headers)
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"

    restored = await client.post(f"/api/v1/admin/articles/{article_id}/restore", headers=headers)
    assert restored.json()["data"]["status"] == "ready"

    ignored = await client.post(f"/api/v1/admin/articles/{article_id}/ignore", headers=headers)
    assert ignored.json()["data"]["status"] == "ignored"

    unauthorized = await client.get("/api/v1/admin/articles")
    assert unauthorized.status_code == 401


@pytest.mark.integration
async def test_article_admin_api_rejects_bad_status_and_unknown_article(
    api: tuple[Any, Any],
) -> None:
    client, _app = api
    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "administrator", "password": "correct-horse-battery-staple"},
    )
    token = initialized.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    bad = await client.get("/api/v1/admin/articles?status=bogus", headers=headers)
    assert bad.status_code == 422

    missing = await client.get("/api/v1/admin/articles/mpa_missing", headers=headers)
    assert missing.status_code == 404
