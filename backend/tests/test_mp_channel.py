from __future__ import annotations

import ipaddress
import json
import struct
import zlib
from typing import Any

import httpx
import pytest
from app.api.errors import AppError
from app.channels.base import ChannelMessage, FakeChannel
from app.channels.mp.adapter import MPArticleAdapter, text_to_html
from app.channels.mp.client import MPClient
from app.config import Settings
from app.domain.clock import SystemClock
from app.infrastructure.database.models import Delivery, Person, WeComIdentity
from app.media.downloader import SafeMediaDownloader
from app.workers.delivery_worker import DeliveryWorker
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload


def make_settings(*, publish_mode: str = "publish") -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        mp_app_id="wx123",
        mp_app_secret=SecretStr("mp-secret"),
        mp_publish_mode=publish_mode,
        mp_author="Test Author",
    )


def make_transport(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://mp.test")


def test_text_to_html_escapes_and_paragraphs() -> None:
    assert text_to_html("hello & <b>bold</b>\n\nsecond") == (
        "<p>hello &amp; &lt;b&gt;bold&lt;/b&gt;</p><p>second</p>"
    )
    assert text_to_html("") == "<p></p>"


@pytest.mark.asyncio
async def test_mp_client_publishes_full_flow() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/cgi-bin/token"):
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 7200})
        if path.endswith("/cgi-bin/material/add_material"):
            return httpx.Response(
                200, json={"media_id": "media-1", "url": "https://mmbiz.qpic.cn/x"}
            )
        if path.endswith("/cgi-bin/draft/add"):
            return httpx.Response(200, json={"media_id": "draft-1"})
        if path.endswith("/cgi-bin/freepublish/submit"):
            return httpx.Response(200, json={"publish_id": 123})
        raise AssertionError(f"unexpected path {path}")

    http = make_transport(handler)
    client = MPClient(make_settings(), SystemClock(), http_client=http)
    try:
        assert await client.get_access_token() == "tok-1"
        assert await client.get_access_token() == "tok-1"
        thumb = await client.upload_permanent_image(
            filename="cover.png", content_type="image/png", content=b"image"
        )
        assert thumb == "media-1"
        draft = await client.add_draft([{"title": "t", "thumb_media_id": thumb}])
        assert draft == "draft-1"
        publish_id = await client.submit_publish(draft)
        assert publish_id == "123"
    finally:
        await http.aclose()

    token_requests = [
        request for request in requests if request.url.path.endswith("/cgi-bin/token")
    ]
    assert len(token_requests) == 1
    for request in requests[1:]:
        assert request.url.params["access_token"] == "tok-1"


@pytest.mark.asyncio
async def test_mp_client_refreshes_invalid_token_once() -> None:
    requests: list[httpx.Request] = []
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        requests.append(request)
        path = request.url.path
        if path.endswith("/cgi-bin/token"):
            token_calls += 1
            token = "tok-2" if token_calls > 1 else "tok-1"
            return httpx.Response(200, json={"access_token": token, "expires_in": 7200})
        if path.endswith("/cgi-bin/draft/add"):
            if request.url.params["access_token"] == "tok-1":
                return httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid credential"})
            return httpx.Response(200, json={"media_id": "draft-1"})
        raise AssertionError(f"unexpected path {path}")

    http = make_transport(handler)
    client = MPClient(make_settings(), SystemClock(), http_client=http)
    try:
        draft = await client.add_draft([{"title": "t"}])
        assert draft == "draft-1"
    finally:
        await http.aclose()

    assert token_calls == 2


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


def _adapter_handler(
    requests: list[httpx.Request],
    *,
    cover_content: bytes | None = None,
    cover_error: Exception | None = None,
    draft_code: int = 0,
    publish_mode: str = "publish",
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/cgi-bin/token"):
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 7200})
        if path.endswith("/cgi-bin/material/add_material"):
            return httpx.Response(
                200, json={"media_id": "media-1", "url": "https://mmbiz.qpic.cn/x"}
            )
        if path.endswith("/cgi-bin/draft/add"):
            if draft_code != 0:
                return httpx.Response(200, json={"errcode": draft_code, "errmsg": "rejected"})
            return httpx.Response(200, json={"media_id": "draft-1"})
        if path.endswith("/cgi-bin/freepublish/submit"):
            assert publish_mode == "publish"
            return httpx.Response(200, json={"publish_id": 123})
        if cover_error is not None:
            raise cover_error
        return httpx.Response(
            200,
            headers={"content-type": "image/png" if cover_content is None else "text/plain"},
            content=_png_bytes() if cover_content is None else cover_content,
        )

    return handler


def article_message(**payload: Any) -> ChannelMessage:
    return ChannelMessage(
        message_type="article",
        title="Codex 用量可能已重置",
        content="第一段\n第二段",
        recipients=[],
        image_url="https://img.example.com/cover.png",
        payload={"publish_to_mp": True, **payload},
    )


@pytest.mark.asyncio
async def test_mp_adapter_publishes_article_and_returns_provider_ids() -> None:
    requests: list[httpx.Request] = []
    http = make_transport(_adapter_handler(requests))
    adapter = MPArticleAdapter(
        MPClient(make_settings(), SystemClock(), http_client=http),
        make_settings(),
        downloader=make_downloader(http),
    )
    try:
        result = await adapter.send(article_message())
    finally:
        await http.aclose()

    assert result.success is True
    assert result.provider_message_id == "draft-1"
    assert result.response_metadata == {
        "publish_id": "123",
        "publish_mode": "publish",
        "article_recorded": False,
    }
    draft_request = next(
        request for request in requests if request.url.path.endswith("/cgi-bin/draft/add")
    )
    article = json.loads(draft_request.content)["articles"][0]
    assert article["title"] == "Codex 用量可能已重置"
    assert article["author"] == "Test Author"
    assert article["thumb_media_id"] == "media-1"
    assert article["content"] == "<p>第一段</p><p>第二段</p>"
    assert article["digest"]


@pytest.mark.asyncio
async def test_mp_adapter_draft_mode_skips_publish() -> None:
    requests: list[httpx.Request] = []
    settings = make_settings(publish_mode="draft")
    http = make_transport(_adapter_handler(requests, publish_mode="draft"))
    adapter = MPArticleAdapter(
        MPClient(settings, SystemClock(), http_client=http),
        settings,
        downloader=make_downloader(http),
    )
    try:
        result = await adapter.send(article_message())
    finally:
        await http.aclose()

    assert result.success is True
    assert result.response_metadata == {
        "draft_only": True,
        "publish_mode": "draft",
        "article_recorded": False,
    }
    assert not any(request.url.path.endswith("/cgi-bin/freepublish/submit") for request in requests)


@pytest.mark.asyncio
async def test_mp_adapter_rejects_non_publish_or_coverless_messages() -> None:
    http = make_transport(_adapter_handler([]))
    adapter = MPArticleAdapter(
        MPClient(make_settings(), SystemClock(), http_client=http),
        make_settings(),
        downloader=make_downloader(http),
    )
    try:
        text = ChannelMessage("text", "t", "c", [])
        assert (await adapter.send(text)).error_code == "PAYLOAD_INVALID"
        without_flag = article_message(publish_to_mp=False)
        assert (await adapter.send(without_flag)).error_code == "PAYLOAD_INVALID"
        coverless = ChannelMessage(
            "article", "t", "c", [], image_url=None, payload={"publish_to_mp": True}
        )
        assert (await adapter.send(coverless)).error_code == "PAYLOAD_INVALID"
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_mp_adapter_rejects_non_image_cover() -> None:
    requests: list[httpx.Request] = []
    http = make_transport(_adapter_handler(requests, cover_content=b"this is not an image"))
    adapter = MPArticleAdapter(
        MPClient(make_settings(), SystemClock(), http_client=http),
        make_settings(),
        downloader=make_downloader(http),
    )
    try:
        result = await adapter.send(article_message())
    finally:
        await http.aclose()

    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "PAYLOAD_INVALID"


@pytest.mark.asyncio
async def test_mp_adapter_maps_provider_rejection_and_network_failure() -> None:
    requests: list[httpx.Request] = []
    settings = make_settings()
    http = make_transport(_adapter_handler(requests, draft_code=40007))
    adapter = MPArticleAdapter(
        MPClient(settings, SystemClock(), http_client=http),
        settings,
        downloader=make_downloader(http),
    )
    try:
        rejected = await adapter.send(article_message())
    finally:
        await http.aclose()
    assert rejected.success is False
    assert rejected.retryable is False
    assert rejected.error_code == "PROVIDER_REJECTED"

    requests = []
    http = make_transport(
        _adapter_handler(requests, cover_error=httpx.ConnectError("network down"))
    )
    adapter = MPArticleAdapter(
        MPClient(settings, SystemClock(), http_client=http),
        settings,
        downloader=make_downloader(http),
    )
    try:
        failed = await adapter.send(article_message())
    finally:
        await http.aclose()
    assert failed.success is False
    assert failed.retryable is True
    assert failed.error_code == "NETWORK_ERROR"


@pytest.mark.asyncio
async def test_mp_adapter_test_checks_credentials() -> None:
    requests: list[httpx.Request] = []
    http = make_transport(_adapter_handler(requests))
    adapter = MPArticleAdapter(
        MPClient(make_settings(), SystemClock(), http_client=http),
        make_settings(),
        downloader=make_downloader(http),
    )
    try:
        result = await adapter.test("unused")
    finally:
        await http.aclose()
    assert result.success is True
    assert any(request.url.path.endswith("/cgi-bin/token") for request in requests)


@pytest.mark.integration
async def test_publish_event_routes_to_mp_article_delivery(api: tuple[Any, Any]) -> None:
    _client, app = api
    result = await app.state.event_service.accept_internal_event(
        source_type="plugin",
        source_id="codex_x_monitor",
        event_type="codex.usage_reset",
        event_key="x-post-1",
        title="Codex 用量可能已重置",
        content="正文",
        recipients=[],
        message_type="article",
        image_url="https://notify.example.com/cover.png",
        payload={"post_id": "1"},
        publish_to_mp=True,
    )
    assert result.duplicate is False

    async with app.state.session_factory() as session:
        rows = list(
            await session.scalars(select(Delivery).options(selectinload(Delivery.notification)))
        )
        assert len(rows) == 1
        delivery = rows[0]
        assert delivery.channel == "mp_article"
        assert delivery.recipient_type == "publish"
        assert delivery.recipient_id is None
        assert delivery.notification.payload.get("publish_to_mp") is True

    fake = FakeChannel()
    worker = DeliveryWorker(
        app.state.session_factory, {"mp_article": fake}, app.state.clock, "worker-test"
    )
    assert await worker.process_one() is True
    assert len(fake.messages) == 1
    message = fake.messages[0]
    assert message.message_type == "article"
    assert message.recipients == []
    assert message.payload.get("publish_to_mp") is True


@pytest.mark.integration
async def test_publish_event_can_also_notify_wecom_recipients(api: tuple[Any, Any]) -> None:
    _client, app = api
    async with app.state.session_factory() as session, session.begin():
        now = app.state.clock.now()
        session.add(
            Person(
                id="person_mp",
                display_name="MP Person",
                active=True,
                is_default=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WeComIdentity(
                id="identity_mp",
                person_id="person_mp",
                user_id="WeComMP",
                active=True,
                created_at=now,
                updated_at=now,
            )
        )

    await app.state.event_service.accept_internal_event(
        source_type="plugin",
        source_id="codex_x_monitor",
        event_type="codex.usage_reset",
        event_key="x-post-2",
        title="Codex 用量可能已重置",
        content="正文",
        recipients=["person_mp"],
        message_type="article",
        image_url="https://notify.example.com/cover.png",
        payload={},
        publish_to_mp=True,
    )

    async with app.state.session_factory() as session:
        rows = list(
            await session.scalars(select(Delivery).options(selectinload(Delivery.notification)))
        )
        assert {row.channel for row in rows} == {"mp_article", "wecom"}
        assert {row.recipient_type for row in rows} == {"publish", "person"}

    wecom_fake = FakeChannel()
    mp_fake = FakeChannel()
    worker = DeliveryWorker(
        app.state.session_factory,
        {"wecom": wecom_fake, "mp_article": mp_fake},
        app.state.clock,
        "worker-test",
    )
    assert await worker.process_one() is True
    assert await worker.process_one() is True
    assert len(wecom_fake.messages) == 1
    assert wecom_fake.messages[0].recipients == ["WeComMP"]
    assert len(mp_fake.messages) == 1
    assert mp_fake.messages[0].payload.get("publish_to_mp") is True


@pytest.mark.integration
async def test_publish_event_requires_article_message(api: tuple[Any, Any]) -> None:
    _client, app = api
    with pytest.raises(AppError, match="requires an article message"):
        await app.state.event_service.accept_internal_event(
            source_type="plugin",
            source_id="codex_x_monitor",
            event_type="codex.usage_reset",
            event_key="x-post-3",
            title="t",
            content="c",
            recipients=[],
            message_type="text",
            publish_to_mp=True,
        )
