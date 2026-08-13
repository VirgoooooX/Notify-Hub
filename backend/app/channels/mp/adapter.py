from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog
from app.application.mp_article_service import MPArticleLibraryService
from app.channels.base import ChannelMessage, ChannelResult
from app.channels.mp.client import MPApiError, MPClient
from app.config import Settings
from app.infrastructure.database.models import MpArticleStatus
from app.media.downloader import SafeMediaDownloader
from app.media.errors import MediaError
from app.media.validation import MediaKind, validate_media

logger = structlog.get_logger()


def text_to_html(text: str) -> str:
    """Render plain notification text as bounded MP article HTML."""
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if not paragraphs:
        paragraphs = [text]
    return "".join(f"<p>{_escape_html(line)}</p>" for line in paragraphs)


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


@dataclass(frozen=True)
class CoverImage:
    content: bytes
    filename: str
    content_type: str


class MPArticleAdapter:
    """Deliver channel-neutral article messages to the WeChat Official Account.

    Two delivery paths are supported:

    - ``library``: store the rendered article in the Notify Hub article workspace
      for manual review and browser import (used automatically when MP API
      credentials are absent, or when ``mp_publish_mode == "library"``);
    - ``api``: upload cover material, create a draft and optionally submit
      publish through the official MP API, while recording the article in the
      workspace as an audit/history entry.
    """

    def __init__(
        self,
        client: MPClient | None,
        settings: Settings,
        downloader: SafeMediaDownloader | None = None,
        library: MPArticleLibraryService | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._downloader = downloader
        self._library = library

    async def send(self, message: ChannelMessage) -> ChannelResult:
        if message.message_type != "article" or message.payload.get("publish_to_mp") is not True:
            return ChannelResult(
                False,
                False,
                "PAYLOAD_INVALID",
                "MP article requires an article message with publish_to_mp",
            )
        if not message.image_url:
            return ChannelResult(
                False,
                False,
                "PAYLOAD_INVALID",
                "MP article requires a cover image",
            )
        if self._library_mode():
            return await self._send_to_library(message)
        return await self._send_via_api(message)

    async def test(self, recipient: str) -> ChannelResult:
        del recipient
        if self._library_mode():
            return ChannelResult(
                True,
                response_metadata={
                    "publish_mode": "library",
                    "manual_publish_required": True,
                },
            )
        try:
            if self._client is None:
                raise RuntimeError("MP credentials are not configured")
            await self._client.get_access_token()
            return ChannelResult(True)
        except (httpx.TimeoutException, httpx.NetworkError):
            return ChannelResult(False, True, "NETWORK_ERROR", "MP network request failed")
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500 or exc.response.status_code == 429
            return ChannelResult(
                False,
                retryable,
                "PROVIDER_TEMPORARY" if retryable else "UNKNOWN_PROVIDER_ERROR",
                f"MP HTTP error {exc.response.status_code}",
                provider_status=exc.response.status_code,
            )
        except (MPApiError, RuntimeError) as exc:
            return ChannelResult(False, False, "AUTH_INVALID", str(exc))

    async def _send_to_library(self, message: ChannelMessage) -> ChannelResult:
        if self._library is None:
            return ChannelResult(
                False,
                False,
                "CHANNEL_NOT_CONFIGURED",
                "MP article library is not available",
            )
        try:
            article_id = await self._library.store_from_delivery(
                delivery_id=message.delivery_id,
                message=message,
                status=MpArticleStatus.READY.value,
            )
        except Exception as exc:
            logger.exception(
                "mp_library_store_failed",
                error_type=type(exc).__name__,
                delivery_id=message.delivery_id,
            )
            return ChannelResult(
                False,
                True,
                "LIBRARY_STORE_FAILED",
                "MP article library store failed",
            )
        return ChannelResult(
            True,
            provider_message_id=article_id,
            response_metadata={
                "article_id": article_id,
                "publish_mode": "library",
                "manual_publish_required": True,
            },
        )

    async def _send_via_api(self, message: ChannelMessage) -> ChannelResult:
        if self._client is None:
            return ChannelResult(
                False,
                False,
                "CHANNEL_NOT_CONFIGURED",
                "MP credentials are not configured",
            )
        try:
            cover = await self._download_cover(str(message.image_url))
            thumb_media_id = await self._client.upload_permanent_image(
                filename=cover.filename,
                content_type=cover.content_type,
                content=cover.content,
            )
            articles = [
                {
                    "title": message.title,
                    "author": self._settings.mp_author,
                    "digest": self._digest(message),
                    "content": text_to_html(message.content),
                    "thumb_media_id": thumb_media_id,
                    "need_open_comment": 0,
                    "only_fans_can_comment": 0,
                }
            ]
            draft_media_id = await self._client.add_draft(articles)
            if self._settings.mp_publish_mode == "draft":
                await self._record_library(
                    message,
                    status=MpArticleStatus.DRAFT.value,
                    provider_draft_media_id=draft_media_id,
                )
                return ChannelResult(
                    True,
                    provider_message_id=draft_media_id,
                    response_metadata={
                        "draft_only": True,
                        "publish_mode": "draft",
                        **self._library_metadata(message),
                    },
                )
            publish_id = await self._client.submit_publish(draft_media_id)
            await self._record_library(
                message,
                status=MpArticleStatus.PUBLISHED.value,
                provider_draft_media_id=draft_media_id,
                provider_publish_id=publish_id,
            )
            return ChannelResult(
                True,
                provider_message_id=draft_media_id,
                response_metadata={
                    "publish_id": publish_id,
                    "publish_mode": "publish",
                    **self._library_metadata(message),
                },
            )
        except MPApiError as exc:
            return ChannelResult(
                False,
                exc.retryable,
                "PROVIDER_TEMPORARY" if exc.retryable else "PROVIDER_REJECTED",
                f"MP rejected the article (code {exc.code})",
                response_metadata={"errcode": exc.code},
            )
        except MediaError as exc:
            if exc.retryable:
                return ChannelResult(False, True, "NETWORK_ERROR", "MP cover download failed")
            return ChannelResult(
                False,
                False,
                "PAYLOAD_INVALID",
                f"MP article cover is invalid ({exc.code})",
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            return ChannelResult(False, True, "NETWORK_ERROR", "MP network request failed")
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500 or exc.response.status_code == 429
            return ChannelResult(
                False,
                retryable,
                "PROVIDER_TEMPORARY" if retryable else "UNKNOWN_PROVIDER_ERROR",
                f"MP HTTP error {exc.response.status_code}",
                provider_status=exc.response.status_code,
            )

    async def _record_library(
        self,
        message: ChannelMessage,
        *,
        status: str,
        provider_draft_media_id: str | None = None,
        provider_publish_id: str | None = None,
    ) -> None:
        if self._library is None:
            return
        try:
            await self._library.store_from_delivery(
                delivery_id=message.delivery_id,
                message=message,
                status=status,
                provider_draft_media_id=provider_draft_media_id,
                provider_publish_id=provider_publish_id,
            )
        except Exception as exc:
            logger.warning(
                "mp_library_record_failed",
                error_type=type(exc).__name__,
                delivery_id=message.delivery_id,
            )

    def _library_metadata(self, message: ChannelMessage) -> dict[str, object]:
        return {"article_recorded": self._library is not None}

    def _library_mode(self) -> bool:
        if self._settings.mp_publish_mode == "library":
            return True
        return not (bool(self._settings.mp_app_id) and self._settings.mp_app_secret is not None)

    def _digest(self, message: ChannelMessage) -> str:
        digest = message.payload.get("article_digest")
        if isinstance(digest, str) and digest.strip():
            return digest.strip()[:120]
        content = " ".join(message.content.split())
        return content[:120]

    async def _download_cover(self, source_url: str) -> CoverImage:
        if self._downloader is None:
            raise MediaError("download_unavailable", "MP cover downloader is not available")
        data = await self._downloader.download(
            source_url, max_bytes=self._settings.media_image_max_bytes
        )
        validated = validate_media(
            data, MediaKind.IMAGE, max_bytes=self._settings.media_image_max_bytes
        )
        return CoverImage(
            content=data,
            filename=f"cover{validated.extension}",
            content_type=validated.mime_type,
        )
