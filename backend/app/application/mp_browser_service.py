"""Service for orchestrating the WeChat Official Account browser publisher."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from app.api.errors import AppError
from app.application.event_service import EventService
from app.application.mp_article_service import MPArticleLibraryService
from app.config import Settings
from app.domain.clock import Clock
from app.infrastructure.database.models import MpArticle, MpArticleStatus

logger = structlog.get_logger()

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_QR_BYTES = 1024 * 1024  # 1 MiB
SESSION_OFFLINE_SECONDS = 45


@dataclass
class BrowserSessionSnapshot:
    state: str
    last_seen_at: datetime | None
    current_article_id: str | None
    incident_id: str | None
    last_error_code: str | None
    last_error_message: str | None
    qr_data_url: str | None


class MPBrowserService:
    """Manages MP Browser publisher sessions, claim checkpoints, and alerts."""

    def __init__(
        self,
        library: MPArticleLibraryService,
        events: EventService,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._library = library
        self._events = events
        self._settings = settings
        self._clock = clock
        self._lock = asyncio.Lock()

        self._state: str = "offline"
        self._current_article_id: str | None = None
        self._incident_id: str | None = None
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None
        self._qr_png_base64: str | None = None
        self._last_seen_at: datetime | None = None
        self._active_auth_incident_id: str | None = None

    async def get_session(self) -> BrowserSessionSnapshot:
        now = self._clock.now()
        async with self._lock:
            effective_state = self._state
            if self._last_seen_at is None or (
                now - self._last_seen_at > timedelta(seconds=SESSION_OFFLINE_SECONDS)
            ):
                effective_state = "offline"

            qr_data_url = None
            if effective_state == "auth_required" and self._qr_png_base64:
                qr_data_url = f"data:image/png;base64,{self._qr_png_base64}"

            return BrowserSessionSnapshot(
                state=effective_state,
                last_seen_at=self._last_seen_at,
                current_article_id=self._current_article_id,
                incident_id=self._incident_id,
                last_error_code=self._last_error_code,
                last_error_message=self._last_error_message,
                qr_data_url=qr_data_url,
            )

    async def update_session(
        self,
        *,
        state: str,
        current_article_id: str | None = None,
        incident_id: str | None = None,
        last_error_code: str | None = None,
        last_error_message: str | None = None,
        qr_png_base64: str | None = None,
    ) -> BrowserSessionSnapshot:
        allowed_states = {
            "offline",
            "starting",
            "ready",
            "publishing",
            "auth_required",
            "error",
        }
        if state not in allowed_states:
            raise AppError("invalid_session_state", f"Invalid session state: {state}", 422)

        cleaned_qr: str | None = None
        if qr_png_base64:
            try:
                decoded = base64.b64decode(qr_png_base64, validate=True)
            except Exception as exc:
                raise AppError("invalid_qr_base64", "QR image must be valid base64", 422) from exc

            if len(decoded) > MAX_QR_BYTES:
                raise AppError("qr_too_large", "QR image exceeds 1 MiB", 422)
            if not decoded.startswith(PNG_MAGIC):
                raise AppError("invalid_qr_format", "QR code must be a PNG image", 422)
            cleaned_qr = qr_png_base64

        now = self._clock.now()
        previous_state: str
        prev_incident_id: str | None

        async with self._lock:
            previous_state = self._state
            prev_incident_id = self._active_auth_incident_id

            self._state = state
            self._current_article_id = current_article_id
            self._incident_id = incident_id
            self._last_error_code = last_error_code
            self._last_error_message = last_error_message[:500] if last_error_message else None
            self._last_seen_at = now

            if state == "auth_required":
                if cleaned_qr:
                    self._qr_png_base64 = cleaned_qr
                self._active_auth_incident_id = incident_id or prev_incident_id or "auth_incident"
            else:
                self._qr_png_base64 = None
                if state in {"ready", "publishing"}:
                    self._active_auth_incident_id = None

        # Emit WeCom alert events outside lock
        if previous_state != "auth_required" and state == "auth_required":
            inc = incident_id or prev_incident_id or "auth_incident"
            await self._emit_alert(
                event_type="system.mp_browser_auth_required",
                event_key=f"mp_auth_{inc}",
                title="【公众号】浏览器自动发布登录失效",
                content=f"微信公众号登录会话已过期，请进入后台扫码恢复登录。\n事件标识：{inc}",
                level="warning",
                payload={"incident_id": inc, "error_code": last_error_code},
            )
        elif previous_state == "auth_required" and state in {"ready", "publishing"}:
            inc = prev_incident_id or "auth_incident"
            await self._emit_alert(
                event_type="system.mp_browser_recovered",
                event_key=f"mp_recovered_{inc}",
                title="【公众号】浏览器自动发布登录已恢复",
                content=f"微信公众号登录已恢复正常，发布队列继续运行。\n事件标识：{inc}",
                level="info",
                payload={"incident_id": inc},
            )

        return await self.get_session()

    async def claim_article(self) -> tuple[MpArticle, int, str, str | None] | None:
        result = await self._library.claim_for_browser()
        if result is not None:
            article, _attempt, _resume, _draft_url = result
            async with self._lock:
                self._current_article_id = article.id
        return result

    async def checkpoint_article(
        self,
        article_id: str,
        *,
        phase: str,
        draft_url: str | None = None,
        provider_draft_id: str | None = None,
    ) -> MpArticle:
        return await self._library.checkpoint_browser(
            article_id,
            phase=phase,
            draft_url=draft_url,
            provider_draft_id=provider_draft_id,
        )

    async def complete_article(
        self,
        article_id: str,
        *,
        provider_draft_id: str | None = None,
        provider_publish_id: str | None = None,
        published_url: str | None = None,
    ) -> MpArticle:
        article = await self._library.complete_browser_publish(
            article_id,
            provider_draft_id=provider_draft_id,
            provider_publish_id=provider_publish_id,
            published_url=published_url,
        )
        async with self._lock:
            if self._current_article_id == article_id:
                self._current_article_id = None
        return article

    async def fail_article(
        self,
        article_id: str,
        *,
        retryable: bool,
        error_code: str,
        error_message: str,
    ) -> MpArticle:
        article = await self._library.fail_browser_publish(
            article_id,
            retryable=retryable,
            error_code=error_code,
            error_message=error_message,
        )
        async with self._lock:
            if self._current_article_id == article_id:
                self._current_article_id = None

        if article.status == MpArticleStatus.FAILED.value:
            bp = article.payload.get("browser_publish") or {}
            attempt = bp.get("attempt_count", 1)
            await self._emit_alert(
                event_type="system.mp_browser_publish_failed",
                event_key=f"mp_failed_{article.id}_{attempt}",
                title=f"【公众号】文章自动发布失败：{article.title[:40]}",
                content=(
                    f"文章《{article.title}》发布失败，已终止重试。\n"
                    f"错误代码：{error_code}\n"
                    f"错误详情：{error_message[:200]}"
                ),
                level="warning",
                payload={
                    "article_id": article.id,
                    "error_code": error_code,
                    "error_message": error_message[:500],
                    "attempt_count": attempt,
                },
            )
        return article

    async def release_for_auth(self, article_id: str) -> MpArticle:
        article = await self._library.release_browser_for_auth(article_id)
        async with self._lock:
            if self._current_article_id == article_id:
                self._current_article_id = None
        return article

    async def _emit_alert(
        self,
        *,
        event_type: str,
        event_key: str,
        title: str,
        content: str,
        level: str,
        payload: dict[str, Any],
    ) -> None:
        recipients = self._settings.mp_browser_alert_recipient_ids
        if not recipients:
            return
        try:
            await self._events.accept_internal_event(
                source_type="system",
                source_id="mp_browser",
                event_type=event_type,
                event_key=event_key,
                title=title,
                content=content,
                recipients=recipients,
                level=level,
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "mp_browser_alert_emit_failed",
                event_type=event_type,
                event_key=event_key,
                error=str(exc),
            )
