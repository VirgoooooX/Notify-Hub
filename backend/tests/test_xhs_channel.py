"""Unit tests for XhsArticleAdapter and BrowserPublisherClient in Notify Hub."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import respx
from app.channels.base import ChannelMessage
from app.channels.browser_publisher.client import (
    BrowserPublisherClient,
    BrowserPublisherError,
    BrowserPublisherTemporaryError,
)
from app.channels.xhs.adapter import XhsArticleAdapter


@pytest.mark.asyncio
async def test_xhs_adapter_not_configured() -> None:
    adapter = XhsArticleAdapter(None)
    msg = ChannelMessage(
        message_type="article",
        title="Test Title",
        content="Test Content",
        recipients=[],
        image_url="https://example.com/cover.jpg",
    )
    result = await adapter.send(msg)
    assert result.success is False
    assert result.error_code == "CHANNEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_xhs_adapter_rejects_title_over_20_chars() -> None:
    client = AsyncMock(spec=BrowserPublisherClient)
    adapter = XhsArticleAdapter(client)
    msg = ChannelMessage(
        message_type="article",
        title="123456789012345678901",  # 21 chars
        content="Test Content",
        recipients=[],
        image_url="https://example.com/cover.jpg",
    )
    result = await adapter.send(msg)
    assert result.success is False
    assert result.error_code == "PAYLOAD_INVALID"
    assert "20 characters" in (result.error_message or "")
    client.submit_job.assert_not_called()


@pytest.mark.asyncio
async def test_xhs_adapter_rejects_empty_images() -> None:
    client = AsyncMock(spec=BrowserPublisherClient)
    adapter = XhsArticleAdapter(client)
    msg = ChannelMessage(
        message_type="article",
        title="Valid Title",
        content="Test Content",
        recipients=[],
        image_url=None,
        payload={"image_urls": []},
    )
    result = await adapter.send(msg)
    assert result.success is False
    assert result.error_code == "PAYLOAD_INVALID"
    assert "between 1 and 18 images" in (result.error_message or "")
    client.submit_job.assert_not_called()


@pytest.mark.asyncio
async def test_xhs_adapter_rejects_more_than_18_images() -> None:
    client = AsyncMock(spec=BrowserPublisherClient)
    adapter = XhsArticleAdapter(client)
    msg = ChannelMessage(
        message_type="article",
        title="Valid Title",
        content="Test Content",
        recipients=[],
        image_url="https://example.com/cover.jpg",
        payload={"image_urls": [f"https://example.com/img{i}.jpg" for i in range(19)]},
    )
    result = await adapter.send(msg)
    assert result.success is False
    assert result.error_code == "PAYLOAD_INVALID"
    client.submit_job.assert_not_called()


@pytest.mark.asyncio
async def test_xhs_adapter_submits_job_successfully() -> None:
    client = AsyncMock(spec=BrowserPublisherClient)
    client.base_url = "http://publisher.test:8790"
    client.submit_job.return_value = {
        "id": "pub_job_123",
        "status": "queued",
        "effective_mode": "draft",
    }
    adapter = XhsArticleAdapter(client)
    msg = ChannelMessage(
        message_type="article",
        title="Codex用量已重置",
        content="正文内容",
        recipients=[],
        image_url="https://example.com/cover.jpg",
        payload={"topics": ["OpenAI", "Codex"]},
        delivery_id="dlv_abc",
    )
    result = await adapter.send(msg)
    assert result.success is True
    assert result.provider_message_id == "pub_job_123"
    assert result.response_metadata["publisher_job_id"] == "pub_job_123"
    assert result.response_metadata["platform"] == "xiaohongshu"
    client.submit_job.assert_called_once_with(
        client_request_id="notify-hub:dlv_abc:xiaohongshu",
        platform="xiaohongshu",
        mode=None,
        title="Codex用量已重置",
        body_text="正文内容",
        image_urls=["https://example.com/cover.jpg"],
        topics=["OpenAI", "Codex"],
        source_url=None,
    )


@pytest.mark.asyncio
async def test_xhs_adapter_temporary_error() -> None:
    client = AsyncMock(spec=BrowserPublisherClient)
    client.submit_job.side_effect = BrowserPublisherTemporaryError("Gateway 502")
    adapter = XhsArticleAdapter(client)
    msg = ChannelMessage(
        message_type="article",
        title="Codex用量已重置",
        content="正文",
        recipients=[],
        image_url="https://example.com/cover.jpg",
    )
    result = await adapter.send(msg)
    assert result.success is False
    assert result.retryable is True
    assert result.error_code == "PUBLISHER_TEMPORARY"


@pytest.mark.asyncio
async def test_xhs_adapter_permanent_error() -> None:
    client = AsyncMock(spec=BrowserPublisherClient)
    client.submit_job.side_effect = BrowserPublisherError("Invalid params")
    adapter = XhsArticleAdapter(client)
    msg = ChannelMessage(
        message_type="article",
        title="Codex用量已重置",
        content="正文",
        recipients=[],
        image_url="https://example.com/cover.jpg",
    )
    result = await adapter.send(msg)
    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "PUBLISHER_ERROR"


@pytest.mark.asyncio
@respx.mock
async def test_browser_publisher_client_http_calls() -> None:
    client = BrowserPublisherClient(
        base_url="http://192.168.31.100:8790",
        access_token="secret-token-123",
    )

    # 1. 202 Accepted
    route = respx.post("http://192.168.31.100:8790/v1/jobs").respond(
        status_code=202,
        json={"id": "job_xyz", "status": "queued"},
    )
    resp = await client.submit_job(
        client_request_id="req_1",
        platform="xiaohongshu",
        title="Title",
        body_text="Body",
        image_urls=["https://example.com/img.png"],
    )
    assert resp["id"] == "job_xyz"
    assert route.calls.last.request.headers["authorization"] == "Bearer secret-token-123"

    # 2. 500 Temporary Error
    respx.post("http://192.168.31.100:8790/v1/jobs").respond(
        status_code=500,
        text="Internal Server Error",
    )
    with pytest.raises(BrowserPublisherTemporaryError):
        await client.submit_job(
            client_request_id="req_2",
            platform="xiaohongshu",
            title="Title",
            body_text="Body",
        )

    # 3. 422 Client Error
    respx.post("http://192.168.31.100:8790/v1/jobs").respond(
        status_code=422,
        json={"detail": "Title exceeds limit"},
    )
    with pytest.raises(BrowserPublisherError):
        await client.submit_job(
            client_request_id="req_3",
            platform="xiaohongshu",
            title="Title",
            body_text="Body",
        )


@pytest.mark.asyncio
async def test_event_service_and_delivery_worker_dual_variants(api: tuple[object, object]) -> None:
    from app.channels.base import FakeChannel
    from app.workers.delivery_worker import DeliveryWorker

    _client, app = api
    event_service = app.state.event_service
    session_factory = app.state.session_factory
    clock = app.state.clock

    mp_variant = {
        "platform": "wechat_mp",
        "title": "WeChat MP In-Depth Title",
        "body_text": "Detailed MP article body text.",
        "image_urls": ["https://example.com/mp_cover.png"],
    }
    xhs_variant = {
        "platform": "xiaohongshu",
        "title": "Codex用量已重置",
        "body_text": "XHS concise note body text.",
        "image_urls": ["https://example.com/xhs_img1.png"],
        "topics": ["OpenAI", "Codex"],
    }

    receipt = await event_service.accept_internal_event(
        source_type="plugin",
        source_id="codex_x_monitor",
        event_type="codex.usage_reset",
        event_key="dual-test-1",
        title="Default Event Title",
        content="Default content",
        recipients=[],
        message_type="article",
        publish_variants=[mp_variant, xhs_variant],
    )
    assert receipt.duplicate is False

    fake_mp = FakeChannel()
    fake_xhs = FakeChannel()
    channels = {
        "mp_article": fake_mp,
        "xhs_article": fake_xhs,
    }
    worker = DeliveryWorker(session_factory, channels, clock, "dual-worker")

    # Claim & process deliveries
    # Delivery 1:
    assert await worker.process_one() is True
    # Delivery 2:
    assert await worker.process_one() is True
    # No more:
    assert await worker.process_one() is False

    assert len(fake_mp.messages) == 1
    assert len(fake_xhs.messages) == 1

    mp_msg = fake_mp.messages[0]
    assert mp_msg.title == "WeChat MP In-Depth Title"
    assert mp_msg.content == "Detailed MP article body text."
    assert mp_msg.image_url == "https://example.com/mp_cover.png"

    xhs_msg = fake_xhs.messages[0]
    assert xhs_msg.title == "Codex用量已重置"
    assert xhs_msg.content == "XHS concise note body text."
    assert xhs_msg.payload.get("topics") == ["OpenAI", "Codex"]
    assert xhs_msg.payload.get("image_urls") == ["https://example.com/xhs_img1.png"]


@pytest.mark.asyncio
async def test_mp_adapter_submits_to_browser_publisher_when_in_browser_mode(
    api: tuple[object, object],
) -> None:
    from app.channels.mp.adapter import MPArticleAdapter
    from app.config import Settings

    _client, app = api
    settings = Settings(
        _env_file=None,
        environment="test",
        mp_publish_mode="browser",
        mp_author="Notify Hub",
    )
    mock_publisher = AsyncMock(spec=BrowserPublisherClient)
    mock_publisher.base_url = "http://192.168.31.100:8790"
    mock_publisher.submit_job.return_value = {"id": "wechat_job_999", "status": "queued"}

    adapter = MPArticleAdapter(
        client=None,
        settings=settings,
        library=app.state.mp_article_library,
        browser_publisher_client=mock_publisher,
    )
    msg = ChannelMessage(
        message_type="article",
        title="WeChat Article Title",
        content="WeChat article content",
        recipients=[],
        image_url="https://example.com/cover.jpg",
        payload={"publish_to_mp": True, "body_html": "<p>WeChat article content</p>"},
        delivery_id="dlv_mp_test",
    )
    result = await adapter.send(msg)
    assert result.success is True
    assert result.response_metadata["publisher_job_id"] == "wechat_job_999"
    assert result.response_metadata["publish_mode"] == "browser"
    mock_publisher.submit_job.assert_called_once_with(
        client_request_id="notify-hub:dlv_mp_test:wechat_mp",
        platform="wechat_mp",
        mode="publish",
        title="WeChat Article Title",
        body_text="WeChat article content",
        body_html="<p>WeChat article content</p>",
        author="Notify Hub",
        digest="WeChat article content",
        image_urls=["https://example.com/cover.jpg"],
        source_url=None,
    )
