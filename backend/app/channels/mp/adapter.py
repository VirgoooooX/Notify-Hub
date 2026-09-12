from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from app.application.mp_article_service import MPArticleLibraryService
from app.channels.base import ChannelMessage, ChannelResult
from app.channels.browser_publisher.client import (
    BrowserPublisherClient,
    BrowserPublisherError,
    BrowserPublisherTemporaryError,
)
from app.channels.mp.client import MPApiError, MPClient
from app.config import Settings
from app.infrastructure.database.models import MpArticleStatus
from app.media.downloader import SafeMediaDownloader
from app.media.errors import MediaError
from app.media.validation import MediaKind, validate_media

logger = structlog.get_logger()

MP_INLINE_IMAGE_MAX_BYTES = 1 * 1024 * 1024
MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]*)\)")
HTML_IMAGE_SRC_RE = re.compile(
    r'(?P<prefix><img\b[^>]*?\s+src\s*=\s*["\'])(?P<src>[^"\']+)'
    r'(?P<suffix>["\'])',
    re.IGNORECASE | re.DOTALL,
)


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

    Three delivery paths are supported:

    - ``library``: store the rendered article in the Notify Hub article workspace
      for manual review and browser import (used automatically when MP API
      credentials are absent, or when ``mp_publish_mode == "library"``);
    - ``browser``: record the article and queue the complete article payload
      for Browser Publisher. Browser Publisher owns the official MP API draft
      creation as well as the final Playwright publication step;
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
        browser_publisher_client: BrowserPublisherClient | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._downloader = downloader
        self._library = library
        self._browser_publisher_client = browser_publisher_client

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
        if self._workspace_mode():
            return await self._send_to_library(message)
        return await self._send_via_api(message)

    async def test(self, recipient: str) -> ChannelResult:
        del recipient
        if self._workspace_mode():
            mode = "browser" if self._settings.mp_publish_mode == "browser" else "library"
            metadata: dict[str, object] = {
                "publish_mode": mode,
                "manual_publish_required": mode == "library",
            }
            if mode == "browser":
                metadata["publisher_configured"] = (
                    self._browser_publisher_client is not None
                    and bool(getattr(self._browser_publisher_client, "configured", True))
                )
            return ChannelResult(
                True,
                response_metadata=metadata,
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
        mode = "browser" if self._settings.mp_publish_mode == "browser" else "library"
        if mode == "browser" and (
            self._browser_publisher_client is None
            or not getattr(self._browser_publisher_client, "configured", True)
        ):
            return ChannelResult(
                False,
                False,
                "CHANNEL_NOT_CONFIGURED",
                "Browser Publisher client is required for MP browser mode",
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
        metadata: dict[str, object] = {
            "article_id": article_id,
            "publish_mode": mode,
            "manual_publish_required": mode == "library",
        }
        if mode == "browser" and self._browser_publisher_client is not None:
            client_req_id = f"notify-hub:{message.delivery_id or 'adhoc'}:wechat_mp"
            cover_urls = [str(message.image_url)] if message.image_url else []
            try:
                body_text, body_html, uploaded_media_ids = await self._prepare_browser_article_body(
                    message
                )
                publish_mode = message.payload.get("mode") or "publish"
                submit_kwargs: dict[str, Any] = {
                    "client_request_id": client_req_id,
                    "platform": "wechat_mp",
                    "mode": publish_mode,
                    "title": message.title,
                    "body_text": body_text,
                    "body_html": body_html,
                    "author": self._settings.mp_author,
                    "digest": self._digest(message),
                    "image_urls": cover_urls,
                    "source_url": message.url,
                }
                if uploaded_media_ids:
                    submit_kwargs["uploaded_media_ids"] = uploaded_media_ids
                res = await self._browser_publisher_client.submit_job(**submit_kwargs)
                metadata["publisher_job_id"] = res.get("id")
                metadata["publisher_job_queued"] = True
                metadata["console_url"] = self._browser_publisher_client.base_url
            except MediaError as exc:
                return ChannelResult(
                    False,
                    exc.retryable,
                    "PUBLISHER_TEMPORARY" if exc.retryable else "PAYLOAD_INVALID",
                    f"MP article inline image is invalid ({exc.code})",
                )
            except BrowserPublisherTemporaryError as exc:
                return ChannelResult(False, True, "PUBLISHER_TEMPORARY", str(exc))
            except BrowserPublisherError as exc:
                return ChannelResult(False, False, "PUBLISHER_ERROR", str(exc))
            except Exception as exc:
                logger.exception("browser_publisher_submit_failed", error=str(exc))
                return ChannelResult(False, True, "UNKNOWN_ERROR", str(exc))
        return ChannelResult(
            True,
            provider_message_id=article_id,
            response_metadata=metadata,
        )

    async def _prepare_browser_article_body(
        self, message: ChannelMessage
    ) -> tuple[str, str | None, list[str]]:
        """Upload HTTP images referenced by the article body to Browser Publisher."""
        body_html = message.payload.get("body_html")
        if isinstance(body_html, str) and body_html.strip():
            references = [match.group("src") for match in HTML_IMAGE_SRC_RE.finditer(body_html)]
            body_format = "html"
            body = body_html
        else:
            body = message.content
            references = [
                self._markdown_image_reference(match.group("target"))
                for match in MARKDOWN_IMAGE_RE.finditer(body)
            ]
            body_format = "text"

        unsupported = [
            source
            for source in references
            if not source or urlsplit(source).scheme not in {"http", "https"}
        ]
        if unsupported:
            raise MediaError(
                "unsupported_media_reference",
                "MP browser article images must use absolute HTTP(S) URLs",
            )

        sources: list[str] = []
        for source in references:
            if source and source not in sources and urlsplit(source).scheme in {"http", "https"}:
                sources.append(source)
        if not sources:
            return message.content, body_html if body_format == "html" else None, []
        if self._downloader is None:
            raise MediaError("download_unavailable", "MP inline image downloader is not available")
        browser_publisher = self._browser_publisher_client
        if browser_publisher is None:
            raise MediaError(
                "upload_unavailable",
                "Browser Publisher inline image upload is not available",
            )
        inline_limit = min(self._settings.media_image_max_bytes, MP_INLINE_IMAGE_MAX_BYTES)
        replacements: dict[str, str] = {}
        uploaded_media_ids: list[str] = []
        for index, source in enumerate(sources, start=1):
            data = await self._downloader.download(source, max_bytes=inline_limit)
            validated = validate_media(data, MediaKind.IMAGE, max_bytes=inline_limit)
            media_id = await browser_publisher.upload_media(
                filename=f"inline-{index}{validated.extension}",
                content_type=validated.mime_type,
                content=data,
            )
            replacements[source] = f"publisher-media://{media_id}"
            uploaded_media_ids.append(media_id)

        if body_format == "html":

            def replace_html_image(match: re.Match[str]) -> str:
                replacement = replacements.get(match.group("src"))
                if replacement is None:
                    return match.group(0)
                return f"{match.group('prefix')}{replacement}{match.group('suffix')}"

            return (
                message.content,
                HTML_IMAGE_SRC_RE.sub(replace_html_image, body),
                uploaded_media_ids,
            )

        def replace_markdown_image(match: re.Match[str]) -> str:
            target = match.group("target").strip()
            reference, title_suffix = self._markdown_image_parts(target)
            replacement = replacements.get(reference)
            if replacement is None:
                return match.group(0)
            return f"![{match.group('alt')}]({replacement}{title_suffix})"

        return MARKDOWN_IMAGE_RE.sub(replace_markdown_image, body), None, uploaded_media_ids

    @staticmethod
    def _markdown_image_reference(target: str) -> str:
        reference, _title_suffix = MPArticleAdapter._markdown_image_parts(target)
        return reference

    @staticmethod
    def _markdown_image_parts(target: str) -> tuple[str, str]:
        value = target.strip()
        if not value:
            return "", ""
        if value.startswith("<"):
            closing = value.find(">")
            if closing >= 0:
                return value[1:closing], value[closing + 1 :]
        parts = value.split(maxsplit=1)
        return parts[0], (f" {parts[1]}" if len(parts) == 2 else "")

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
            body_html = message.payload.get("body_html")
            if not isinstance(body_html, str) or not body_html.strip():
                body_html = text_to_html(message.content)
            articles = [
                {
                    "title": message.title,
                    "author": self._settings.mp_author,
                    "digest": self._digest(message),
                    "content": body_html,
                    "thumb_media_id": thumb_media_id,
                    "need_open_comment": 1,
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

    def _workspace_mode(self) -> bool:
        # Browser Publisher owns both the official API draft phase and the
        # Playwright publication phase. Notify Hub only records and dispatches
        # the article payload in this mode, regardless of legacy MP secrets
        # that may still exist in its environment.
        if self._settings.mp_publish_mode in {"library", "browser"}:
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
