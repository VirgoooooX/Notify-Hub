"""Main worker loop for the MP Playwright browser publisher."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.mp_browser.api_client import MPBrowserApiClient
from app.mp_browser.config import MPBrowserSettings
from app.mp_browser.wechat import WeChatPublisher

logger = structlog.get_logger()

MAX_SAVED_SCREENSHOTS = 20


def clean_artifacts(artifacts_dir: Path) -> None:
    """Keep only the most recent MAX_SAVED_SCREENSHOTS files to prevent disk exhaustion."""
    try:
        if not artifacts_dir.exists():
            return
        files = sorted(
            [p for p in artifacts_dir.iterdir() if p.is_file() and p.suffix == ".png"],
            key=lambda p: p.stat().st_mtime,
        )
        if len(files) > MAX_SAVED_SCREENSHOTS:
            for old_file in files[:-MAX_SAVED_SCREENSHOTS]:
                try:
                    old_file.unlink()
                except OSError as exc:
                    logger.debug("clean_artifact_file_failed", file=str(old_file), error=str(exc))
    except Exception as exc:
        logger.debug("clean_artifacts_failed", error=str(exc))


def parse_error_code(exc: Exception) -> str:
    msg = f"{type(exc).__name__} {exc}".upper()
    known = [
        "SECURITY_CHECK_TRIGGERED",
        "RATE_LIMIT_TRIGGERED",
        "AUTH_REQUIRED",
        "EDITOR_NOT_FOUND",
        "EDITOR_TIMEOUT",
        "CONTENT_REJECTED",
        "COVER_FAILED",
        "DRAFT_SAVE_FAILED",
        "PUBLISH_QUOTA_EXHAUSTED",
        "PUBLISH_CONFIRM_FAILED",
        "PUBLISH_RESULT_UNKNOWN",
        "PROVIDER_UI_CHANGED",
        "NETWORK_ERROR",
    ]
    for code in known:
        if code in msg:
            return code
    if "TIMEOUT" in msg or "TIMED OUT" in msg:
        return "EDITOR_TIMEOUT"
    if "HTTP" in msg or "CONNECTION" in msg or "NETWORK" in msg:
        return "NETWORK_ERROR"
    return "PROVIDER_UI_CHANGED"


class MPBrowserWorker:
    """Orchestrates persistent browser session, queue claims, and publishing actions."""

    def __init__(
        self,
        settings: MPBrowserSettings,
        api_client: MPBrowserApiClient,
        publisher: WeChatPublisher,
    ) -> None:
        self._settings = settings
        self._api = api_client
        self._publisher = publisher
        self._running = False
        self._paused = False
        self._paused_reason: str | None = None
        self._current_article_id: str | None = None
        self._incident_id: str | None = None
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None

    async def run(self) -> None:
        self._settings.ensure_directories()
        clean_artifacts(self._settings.artifacts_dir)
        self._running = True

        logger.info("mp_browser_worker_starting")
        try:
            await self._api.update_session(state="starting")
        except Exception as exc:
            logger.warning("initial_session_update_failed", error=str(exc))

        await self._publisher.start()

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            await self._main_loop()
        finally:
            self._running = False
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            await self._publisher.close()
            await self._api.close()
            logger.info("mp_browser_worker_stopped")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._settings.heartbeat_seconds)
                state = "ready"
                if self._paused:
                    state = "error"
                elif self._incident_id:
                    state = "auth_required"
                elif self._current_article_id:
                    state = "publishing"

                await self._api.update_session(
                    state=state,
                    current_article_id=self._current_article_id,
                    incident_id=self._incident_id,
                    last_error_code=self._last_error_code,
                    last_error_message=self._last_error_message or self._paused_reason,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("heartbeat_failed", error=str(exc))

    async def _main_loop(self) -> None:
        page = await self._publisher.get_page()

        while self._running:
            # 0. If paused due to risk control, skip claiming and wait for operator restart
            if self._paused:
                await asyncio.sleep(self._settings.poll_seconds)
                continue

            # 1. Verify authentication
            logged_in = await self._publisher.check_login(page)
            if not logged_in:
                if not self._incident_id:
                    self._incident_id = uuid.uuid4().hex
                qr_base64 = await self._publisher.capture_login_qr(page)
                try:
                    await self._api.update_session(
                        state="auth_required",
                        incident_id=self._incident_id,
                        last_error_code="AUTH_REQUIRED",
                        last_error_message="WeChat session expired or not authenticated",
                        qr_png_base64=qr_base64,
                    )
                except Exception as exc:
                    logger.debug("auth_session_report_failed", error=str(exc))
                await asyncio.sleep(5.0)
                continue

            # Logged in
            if self._incident_id:
                self._incident_id = None
                self._last_error_code = None
                self._last_error_message = None
                try:
                    await self._api.update_session(state="ready")
                except Exception as sess_exc:
                    logger.debug("session_ready_update_failed", error=str(sess_exc))

            # 2. Claim article
            claimed: dict[str, Any] | None = None
            try:
                claimed = await self._api.claim()
            except Exception as exc:
                logger.warning("claim_request_failed", error=str(exc))
                await asyncio.sleep(self._settings.poll_seconds)
                continue

            if claimed is None:
                await asyncio.sleep(self._settings.poll_seconds)
                continue

            # 3. Process claimed article
            article = claimed["article"]
            article_id = article["id"]
            resume = claimed.get("resume", "new")
            draft_url = claimed.get("draft_url")
            self._current_article_id = article_id

            logger.info("article_claimed", article_id=article_id, resume=resume)
            try:
                await self._process_article(page, article, resume=resume, draft_url=draft_url)
            except Exception as exc:
                await self._handle_article_failure(page, article_id, exc)
            finally:
                self._current_article_id = None

    async def _process_article(
        self,
        page: Any,
        article: dict[str, Any],
        *,
        resume: str,
        draft_url: str | None,
    ) -> None:
        article_id = article["id"]
        title = article["title"]
        start_time = datetime.now(UTC)
        draft_media_id: str | None = None

        if resume == "new":
            editor_page = await self._publisher.open_editor(page)
            try:
                await self._publisher.fill_article(editor_page, article)
                await self._publisher.select_cover_from_content(editor_page)
                saved_url, draft_media_id = await self._publisher.save_draft(editor_page)

                await self._api.checkpoint(
                    article_id,
                    phase="draft_saved",
                    draft_url=saved_url,
                    provider_draft_id=draft_media_id,
                )

                if self._settings.draft_only:
                    logger.info("mp_browser_draft_only_complete", article_id=article_id)
                    await self._api.complete(
                        article_id,
                        status="draft",
                        provider_draft_id=draft_media_id,
                        published_url=saved_url,
                    )
                    return

                await self._api.checkpoint(article_id, phase="publish_intent")
                await self._publisher.check_risk_control(editor_page)
                await self._publisher.click_publish_and_confirm(editor_page)
                await self._api.checkpoint(article_id, phase="publish_clicked")

                published_url = await self._publisher.verify_published(
                    editor_page, title, start_time
                )
                if published_url:
                    await self._api.complete(
                        article_id,
                        provider_draft_id=draft_media_id,
                        published_url=published_url,
                    )
                else:
                    await self._api.fail(
                        article_id,
                        retryable=False,
                        error_code="PUBLISH_RESULT_UNKNOWN",
                        error_message=(
                            "Mass send confirmed but published URL could not be retrieved"
                        ),
                    )
            finally:
                if editor_page != page:
                    try:
                        await editor_page.close()
                    except Exception as close_exc:
                        logger.debug("editor_page_close_failed", error=str(close_exc))

        elif resume == "resume":
            if self._settings.draft_only:
                logger.info("mp_browser_draft_only_resume_complete", article_id=article_id)
                await self._api.complete(
                    article_id,
                    status="draft",
                    published_url=draft_url,
                )
                return

            # Quick check if it was already published in a previous attempt
            quick_pub = await self._publisher.reconcile(page, title, start_time, max_seconds=10)
            if quick_pub:
                await self._api.complete(article_id, published_url=quick_pub)
                return

            if not draft_url:
                raise RuntimeError("DRAFT_SAVE_FAILED: Resume requested but draft_url is missing")
            await self._publisher.open_draft(page, draft_url)
            await self._api.checkpoint(article_id, phase="publish_intent")
            await self._publisher.check_risk_control(page)
            await self._publisher.click_publish_and_confirm(page)
            await self._api.checkpoint(article_id, phase="publish_clicked")

            published_url = await self._publisher.verify_published(page, title, start_time)
            if published_url:
                await self._api.complete(
                    article_id,
                    published_url=published_url,
                )
            else:
                await self._api.fail(
                    article_id,
                    retryable=False,
                    error_code="PUBLISH_RESULT_UNKNOWN",
                    error_message="Draft resumed and published but result URL unconfirmed",
                )

        elif resume == "reconcile":
            # Reconcile only: forbidden from clicking publish button again
            published_url = await self._publisher.reconcile(
                page, title, start_time, max_seconds=120
            )
            if published_url:
                await self._api.complete(article_id, published_url=published_url)
            else:
                await self._api.fail(
                    article_id,
                    retryable=False,
                    error_code="PUBLISH_RESULT_UNKNOWN",
                    error_message="Reconcile could not locate article in published list",
                )

    async def _handle_article_failure(self, page: Any, article_id: str, exc: Exception) -> None:
        err_code = parse_error_code(exc)
        err_msg = str(exc)[:500]
        self._last_error_code = err_code
        self._last_error_message = err_msg

        # Capture screenshot for diagnosis
        try:
            timestamp = int(time.time())
            screenshot_path = self._settings.artifacts_dir / f"{article_id}-{timestamp}.png"
            await page.screenshot(path=str(screenshot_path))
            clean_artifacts(self._settings.artifacts_dir)
        except Exception as ss_exc:
            logger.debug("failure_screenshot_failed", error=str(ss_exc))

        # If security check or rate limit was triggered, pause worker immediately
        if err_code in {"SECURITY_CHECK_TRIGGERED", "RATE_LIMIT_TRIGGERED"}:
            self._paused = True
            self._paused_reason = f"{err_code}: {err_msg}"
            logger.error("mp_browser_paused_due_to_risk", code=err_code, message=err_msg)
            try:
                await self._api.update_session(
                    state="error",
                    incident_id=self._incident_id,
                    last_error_code=err_code,
                    last_error_message=err_msg,
                )
            except Exception as sess_exc:
                logger.debug("session_risk_error_update_failed", error=str(sess_exc))

        # If auth expired during execution, release without attempt penalty
        if err_code == "AUTH_REQUIRED":
            try:
                await self._api.release_for_auth(article_id)
            except Exception as rel_exc:
                logger.debug("release_for_auth_failed", error=str(rel_exc))
            return

        # Fail article with retryable=True unless it was a permanent issue, quota, or risk controls
        non_retryable_codes = {
            "CONTENT_REJECTED",
            "PUBLISH_QUOTA_EXHAUSTED",
            "PUBLISH_CONFIRM_FAILED",
            "SECURITY_CHECK_TRIGGERED",
            "RATE_LIMIT_TRIGGERED",
        }
        retryable = err_code not in non_retryable_codes
        try:
            await self._api.fail(
                article_id,
                retryable=retryable,
                error_code=err_code,
                error_message=err_msg,
            )
        except Exception as fail_exc:
            logger.warning("api_fail_report_failed", error=str(fail_exc))


async def run() -> None:
    settings = MPBrowserSettings()
    api_client = MPBrowserApiClient(settings)
    publisher = WeChatPublisher(settings)
    worker = MPBrowserWorker(settings, api_client, publisher)
    await worker.run()
