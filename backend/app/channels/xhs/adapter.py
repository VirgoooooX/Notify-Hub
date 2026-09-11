"""Xiaohongshu article channel adapter forwarding publishing tasks to Browser Publisher."""

from __future__ import annotations

import structlog
from app.channels.base import ChannelMessage, ChannelResult
from app.channels.browser_publisher.client import (
    BrowserPublisherClient,
    BrowserPublisherError,
    BrowserPublisherTemporaryError,
)

logger = structlog.get_logger()


class XhsArticleAdapter:
    """Deliver channel-neutral article messages to Xiaohongshu via Browser Publisher."""

    def __init__(self, client: BrowserPublisherClient | None = None) -> None:
        self._client = client

    async def send(self, message: ChannelMessage) -> ChannelResult:
        if self._client is None or not getattr(self._client, "configured", True):
            return ChannelResult(
                False,
                False,
                "CHANNEL_NOT_CONFIGURED",
                "Browser Publisher client is not configured for Xiaohongshu",
            )

        payload = message.payload or {}
        image_urls = list(payload.get("image_urls", []))
        if message.image_url and message.image_url not in image_urls:
            image_urls.insert(0, message.image_url)

        if not (1 <= len(image_urls) <= 18):
            return ChannelResult(
                False,
                False,
                "PAYLOAD_INVALID",
                f"Xiaohongshu requires between 1 and 18 images (got {len(image_urls)})",
            )

        title = message.title.strip()
        if len(title) > 20:
            return ChannelResult(
                False,
                False,
                "PAYLOAD_INVALID",
                f"Xiaohongshu note title cannot exceed 20 characters (got {len(title)})",
            )

        delivery_id = message.delivery_id or "adhoc"
        client_req_id = f"notify-hub:{delivery_id}:xiaohongshu"

        try:
            res = await self._client.submit_job(
                client_request_id=client_req_id,
                platform="xiaohongshu",
                mode=payload.get("mode"),
                visibility=payload.get("visibility") or "public",
                title=title,
                body_text=message.content,
                image_urls=image_urls,
                topics=payload.get("topics", []),
                source_url=message.url,
            )
            job_id = res["id"]
            return ChannelResult(
                True,
                provider_message_id=job_id,
                response_metadata={
                    "publisher_job_id": job_id,
                    "platform": "xiaohongshu",
                    "effective_mode": res.get("effective_mode"),
                    "console_url": self._client.base_url,
                },
            )
        except BrowserPublisherTemporaryError as exc:
            return ChannelResult(False, True, "PUBLISHER_TEMPORARY", str(exc))
        except BrowserPublisherError as exc:
            return ChannelResult(False, False, "PUBLISHER_ERROR", str(exc))
        except Exception as exc:
            logger.exception("xhs_publish_unexpected_error", error=str(exc))
            return ChannelResult(False, True, "UNKNOWN_ERROR", str(exc))

    async def test(self, recipient: str) -> ChannelResult:
        del recipient
        if self._client is None or not getattr(self._client, "configured", True):
            return ChannelResult(False, False, "CHANNEL_NOT_CONFIGURED", "Client not configured")
        return ChannelResult(True, response_metadata={"platform": "xiaohongshu"})
