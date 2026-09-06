"""Playwright automation adapter for the WeChat Official Account web management UI."""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import structlog

from app.mp_browser.config import MPBrowserSettings

logger = structlog.get_logger()

# Selectors and constants
MP_ORIGIN = "https://mp.weixin.qq.com"
MP_HOME_URL = f"{MP_ORIGIN}/"
QR_SELECTORS = [
    "img.js_login_qrcode",
    ".js_login_qrcode img",
    'img[src*="scanloginqrcode"]',
    ".login__type__container img",
]
CONFIRM_BUTTON_NAMES = ["确认", "继续发表", "确定", "群发"]


class WeChatPublisher:
    """Automates article draft creation, cover selection, and publication on WeChat MP."""

    def __init__(self, settings: MPBrowserSettings) -> None:
        self._settings = settings
        self._playwright: Any = None
        self._context: Any = None

    async def start(self) -> None:
        """Launch persistent browser context with required permissions."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._settings.profile_dir),
            headless=self._settings.headless,
            viewport={"width": 1440, "height": 1100},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        try:
            await self._context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=MP_ORIGIN,
            )
        except Exception:
            logger.warning("failed_to_grant_clipboard_permissions")

    async def close(self) -> None:
        """Close browser context and stop Playwright."""
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as exc:
                logger.debug("browser_context_close_failed", error=str(exc))
            self._context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.debug("playwright_stop_failed", error=str(exc))
            self._playwright = None

    async def get_page(self) -> Any:
        if not self._context:
            raise RuntimeError("Playwright browser context is not initialized")
        pages = self._context.pages
        return pages[0] if pages else await self._context.new_page()

    async def check_login(self, page: Any) -> bool:
        """Navigate to MP home and check if session is authenticated."""
        try:
            if MP_ORIGIN not in page.url:
                await page.goto(
                    MP_HOME_URL,
                    timeout=int(self._settings.navigation_timeout_seconds * 1000),
                    wait_until="domcontentloaded",
                )
            url = page.url
            if "/cgi-bin/" in url and "login" not in url:
                return True

            account_info = page.locator(".weui-desktop-account__info")
            if await account_info.count() > 0 and await account_info.first.is_visible():
                return True

            new_create = page.get_by_text("新的创作", exact=False)
            if await new_create.count() > 0 and await new_create.first.is_visible():
                return True

            draft_hint = page.get_by_text("近期草稿", exact=False)
            if await draft_hint.count() > 0 and await draft_hint.first.is_visible():
                return True
        except Exception as exc:
            logger.debug("check_login_failed", error=str(exc))
        return False

    async def capture_login_qr(self, page: Any) -> str | None:
        """Locate and capture the login QR code image snippet as base64 PNG."""
        if MP_ORIGIN not in page.url:
            await page.goto(
                MP_HOME_URL,
                timeout=int(self._settings.navigation_timeout_seconds * 1000),
                wait_until="domcontentloaded",
            )
        for selector in QR_SELECTORS:
            qr_locator = page.locator(selector)
            if await qr_locator.count() > 0 and await qr_locator.first.is_visible():
                try:
                    png_bytes = await qr_locator.first.screenshot(type="png")
                    return base64.b64encode(png_bytes).decode("ascii")
                except Exception as exc:
                    logger.debug("qr_screenshot_failed", selector=selector, error=str(exc))
        return None

    async def open_editor(self, page: Any) -> Any:
        """Open the article editor from the home dashboard or direct navigation."""
        if "/cgi-bin/" not in page.url:
            await page.goto(
                MP_HOME_URL,
                timeout=int(self._settings.navigation_timeout_seconds * 1000),
                wait_until="domcontentloaded",
            )

        # Try clicking the "文章" create button
        new_article_btn = page.locator('.new-creation__menu-item:has-text("文章"), .appmsg_edit')
        if await new_article_btn.count() == 0:
            new_article_btn = page.get_by_text("文章", exact=True)
        if await new_article_btn.count() > 0 and await new_article_btn.first.is_visible():
            async with self._context.expect_page(timeout=15000) as page_info:
                await new_article_btn.first.click()
            editor_page = await page_info.value
            await editor_page.wait_for_load_state("domcontentloaded")
            return editor_page

        # Fallback: check if current page is already in editor
        if "/cgi-bin/appmsg" in page.url:
            return page

        raise RuntimeError("EDITOR_NOT_FOUND: Could not open article editor from dashboard")

    async def fill_article(self, page: Any, article: dict[str, Any]) -> None:
        """Fill title, author, digest, and rich text body into editor."""
        title = article.get("title", "")
        author = article.get("author", "")
        digest = article.get("digest", "")
        content_html = article.get("content_html", "")
        content_text = article.get("content", "")

        # 1. Title (WeChat MP modern editor uses ProseMirror div with data-placeholder)
        title_filled = False
        title_locators = [
            page.locator('div.ProseMirror[data-placeholder*="标题"]'),
            page.locator('.ProseMirror[data-placeholder*="标题"]'),
            page.get_by_role("textbox", name="请在这里输入标题"),
            page.locator('input[placeholder*="标题"]'),
            page.locator('textarea[placeholder*="标题"]'),
            page.locator("#title"),
            page.locator("#appmsg_title"),
        ]
        for loc in title_locators:
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click()
                    await loc.first.fill(title)
                    title_filled = True
                    break
            except Exception as exc:
                logger.debug("title_locator_try_failed", error=str(exc))

        if not title_filled:
            primary_title = page.locator('div.ProseMirror[data-placeholder*="标题"]')
            try:
                await primary_title.first.wait_for(state="visible", timeout=8000)
                await primary_title.first.click()
                await primary_title.first.fill(title)
                title_filled = True
            except Exception as exc:
                logger.debug("primary_title_fill_failed", error=str(exc))

        if not title_filled:
            raise RuntimeError("EDITOR_NOT_FOUND: Title input not found")

        # 2. Author
        if author:
            author_locators = [
                page.locator("#author"),
                page.get_by_role("textbox", name="请输入作者"),
                page.locator('input[placeholder*="作者"]'),
            ]
            for loc in author_locators:
                try:
                    if await loc.count() > 0 and await loc.first.is_visible():
                        await loc.first.fill(author)
                        break
                except Exception as exc:
                    logger.debug("author_locator_try_failed", error=str(exc))

        # 3. Digest
        if digest:
            digest_locators = [
                page.locator("#js_summary"),
                page.locator(".js_description"),
                page.locator('textarea[placeholder*="摘要"]'),
            ]
            for loc in digest_locators:
                try:
                    if await loc.count() > 0 and await loc.first.is_visible():
                        await loc.first.fill(digest)
                        break
                except Exception as exc:
                    logger.debug("digest_locator_try_failed", error=str(exc))

        # 4. Rich text body (target body editor, explicitly distinct from title ProseMirror)
        editor_locators = [
            page.locator("#ueditor_0 .ProseMirror"),
            page.locator(".rich_media_content .ProseMirror"),
            page.locator('div.ProseMirror:not([data-placeholder*="标题"])'),
            page.locator("#js_editor_content"),
            page.locator("#js_editor"),
        ]
        editor_target = None
        for loc in editor_locators:
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    editor_target = loc.first
                    break
            except Exception as exc:
                logger.debug("editor_locator_try_failed", error=str(exc))

        if editor_target is None:
            # Check for iframe editor body
            frames = page.frames
            for frame in frames:
                frame_body = frame.locator("body.view, body.uneditable, body")
                if await frame_body.count() > 0 and await frame_body.first.is_visible():
                    editor_target = frame_body.first
                    break

        if editor_target is None:
            raise RuntimeError("EDITOR_NOT_FOUND: Rich text editor contenteditable not found")

        await editor_target.click()
        pasted = False
        try:
            # Execute clipboard paste via Web API
            await page.evaluate(
                """([html, text]) => {
                    const item = new ClipboardItem({
                        "text/html": new Blob([html], { type: "text/html" }),
                        "text/plain": new Blob([text], { type: "text/plain" })
                    });
                    return navigator.clipboard.write([item]);
                }""",
                [content_html, content_text],
            )
            await editor_target.focus()
            await page.keyboard.press("Control+V")
            await asyncio.sleep(1.0)
            text_len = len(await editor_target.inner_text())
            if text_len > 10:
                pasted = True
        except Exception as exc:
            logger.debug("clipboard_paste_failed", error=str(exc))

        if not pasted:
            # Fallback to direct DOM manipulation or fill
            try:
                if content_html:
                    await page.evaluate(
                        """([editor, html]) => {
                            editor.innerHTML = html;
                            editor.dispatchEvent(new Event("input", { bubbles: true }));
                            editor.dispatchEvent(new Event("change", { bubbles: true }));
                        }""",
                        [await editor_target.element_handle(), content_html],
                    )
                else:
                    await editor_target.fill(content_text)
            except Exception as dom_exc:
                logger.debug("body_dom_fallback_failed", error=str(dom_exc))
                if content_text:
                    await editor_target.fill(content_text)
            await asyncio.sleep(0.5)

    async def select_cover_from_content(self, page: Any) -> None:
        """Select the first body image as article cover via '从正文选择'."""
        # 1. Trigger cover selection modal
        cover_btn = page.locator(
            ".select-cover__btn, #js_cover_area, .js_cover_btn_area, #js_cover_null"
        ).first
        if await cover_btn.count() == 0:
            logger.warning("cover_selection_button_not_found_skipping")
            return
        try:
            await cover_btn.click()
            await asyncio.sleep(1.0)
        except Exception as exc:
            logger.warning("click_cover_btn_failed", error=str(exc))
            return

        # 2. Click '从正文选择' option in dropdown
        from_content_tab = page.locator(':has-text("从正文选择")').last
        if await from_content_tab.count() == 0 or not await from_content_tab.is_visible():
            logger.warning("from_content_tab_not_found_skipping")
            return
        try:
            await from_content_tab.click()
            await asyncio.sleep(1.5)
        except Exception as exc:
            logger.warning("click_from_content_tab_failed", error=str(exc))
            return

        # 3. Pick candidate image in modal
        img_pick = page.locator(
            ".appmsg_content_img_item, .appmsg_content_img, "
            ".img_crop_panel .appmsg_content_img, .weui-desktop-picture-check"
        ).first
        if await img_pick.count() == 0 or not await img_pick.is_visible():
            logger.warning("no_candidate_image_found_in_tab_skipping")
            # Close dialog if open
            close_btn = page.locator(
                '.weui-desktop-dialog:has-text("选择图片") button:has-text("取消")'
            )
            if await close_btn.count() > 0 and await close_btn.is_visible():
                await close_btn.first.click()
            return
        await img_pick.click()
        await asyncio.sleep(1.0)

        # 4. Click '下一步' to proceed to crop
        next_btn = page.locator('button:has-text("下一步"):not(.weui-desktop-btn_disabled)').first
        if await next_btn.count() == 0 or not await next_btn.is_visible():
            logger.warning("cover_next_button_not_found_skipping")
            return
        await next_btn.click()
        await asyncio.sleep(2.0)

        # 5. Confirm crop modal with '确认', '完成' or '确定'
        dialog_confirmed = False
        for btn_name in ["确认", "完成", "确定"]:
            confirm_btn = page.locator(
                f'.weui-desktop-dialog:has-text("编辑封面") button:has-text("{btn_name}"), '
                f'.weui-desktop-dialog:not([style*="display: none"]) button:has-text("{btn_name}")'
            ).first
            if await confirm_btn.count() > 0 and await confirm_btn.is_visible():
                await confirm_btn.click()
                dialog_confirmed = True
                await asyncio.sleep(1.5)
                break

        if not dialog_confirmed:
            logger.warning("cover_confirmation_button_not_found")
            return

        # 6. Verify cover preview appears
        preview = page.locator(
            ".js_cover_preview_new, .js_cover_preview, "
            ".appmsg_cover_preview, .setting-group__cover_primary"
        )
        try:
            await preview.first.wait_for(state="visible", timeout=5000)
        except Exception as exc:
            logger.debug("cover_preview_wait_ignored", error=str(exc))

    async def save_draft(self, page: Any) -> tuple[str, str | None]:
        """Click '保存为草稿', wait for explicit save signal, and extract draft identity."""
        save_btn = page.get_by_role("button", name="保存为草稿")
        if await save_btn.count() == 0:
            save_btn = page.locator('button:has-text("保存为草稿")')
        if await save_btn.count() == 0:
            save_btn = page.get_by_text("保存为草稿", exact=True)
        if await save_btn.count() == 0:
            raise RuntimeError("DRAFT_SAVE_FAILED: '保存为草稿' button not found")

        # Listen for draft save response
        draft_media_id: str | None = None

        async def handle_response(res: Any) -> None:
            nonlocal draft_media_id
            if "appmsg" in res.url and res.request.method == "POST":
                try:
                    data = await res.json()
                    if isinstance(data, dict):
                        mid = data.get("appmsgid") or data.get("appMsgId")
                        if mid:
                            draft_media_id = str(mid)
                except Exception as exc:
                    logger.debug("parse_appmsg_response_failed", error=str(exc))

        page.on("response", handle_response)
        try:
            await save_btn.first.click()
            # Wait for draft ID in URL, or response, or saved indicator
            start_wait = time.time()
            max_wait = float(self._settings.operation_timeout_seconds)
            while time.time() - start_wait < max_wait:
                if draft_media_id or "appmsgid=" in page.url:
                    break
                saved_indicator = page.locator(
                    '.weui-desktop-toast, :has-text("已保存"), '
                    ':has-text("保存成功"), :has-text("手动保存")'
                )
                if await saved_indicator.count() > 0 and await saved_indicator.first.is_visible():
                    break
                await asyncio.sleep(0.5)
        finally:
            page.remove_listener("response", handle_response)

        draft_url = page.url
        # Fallback: extract appmsgid from URL query parameters
        if not draft_media_id and "appmsgid=" in draft_url:
            qs = parse_qs(urlsplit(draft_url).query)
            appmsgid_vals = qs.get("appmsgid")
            if appmsgid_vals:
                draft_media_id = appmsgid_vals[0]

        return draft_url, draft_media_id

    async def open_draft(self, page: Any, draft_url: str) -> None:
        """Open previously saved draft by URL with domain validation."""
        parts = urlsplit(draft_url)
        if parts.scheme != "https" or parts.netloc != "mp.weixin.qq.com":
            raise ValueError(f"DRAFT_SAVE_FAILED: Invalid draft URL domain or scheme: {draft_url}")
        await page.goto(
            draft_url,
            timeout=int(self._settings.navigation_timeout_seconds * 1000),
            wait_until="domcontentloaded",
        )

    async def click_publish_and_confirm(self, page: Any) -> None:
        """Click 'button.mass_send' and confirm publication dialog."""
        mass_send_btn = page.locator("button.mass_send")
        if await mass_send_btn.count() == 0:
            mass_send_btn = page.get_by_role("button", name="发表")
        if await mass_send_btn.count() == 0:
            mass_send_btn = page.get_by_role("button", name="群发")
        if await mass_send_btn.count() == 0:
            raise RuntimeError("PUBLISH_CONFIRM_FAILED: Publish / mass_send button not found")

        await mass_send_btn.first.click()
        await asyncio.sleep(1.0)

        # Check for AI declaration if platform displays one
        ai_option = page.locator(':has-text("AI 辅助生成"), :has-text("AI 生成")')
        if await ai_option.count() > 0 and await ai_option.first.is_visible():
            await ai_option.first.click()
            await asyncio.sleep(0.5)

        # In confirmation dialog, click ONLY whitelisted button texts
        confirmed = False
        for name in CONFIRM_BUTTON_NAMES:
            confirm_btn = page.locator(
                f'.weui-desktop-dialog button:has-text("{name}"), '
                f'.weui-desktop-modal button:has-text("{name}"), '
                f'button.weui-desktop-btn_primary:has-text("{name}")'
            )
            if await confirm_btn.count() > 0 and await confirm_btn.first.is_visible():
                await confirm_btn.first.click()
                confirmed = True
                break

        if not confirmed:
            raise RuntimeError("PUBLISH_CONFIRM_FAILED: Confirmation modal button not found")

    async def verify_published(self, page: Any, title: str, start_time: datetime) -> str | None:
        """Verify publication success and extract published article URL."""
        # Inspect published list to retrieve public URL
        return await self.reconcile(page, title, start_time, max_seconds=30)

    async def reconcile(
        self,
        page: Any,
        title: str,
        start_time: datetime,
        max_seconds: int = 120,
    ) -> str | None:
        """Check published list repeatedly up to max_seconds for title match."""
        del start_time
        deadline = asyncio.get_event_loop().time() + max_seconds
        while asyncio.get_event_loop().time() < deadline:
            try:
                # Look for published tab or navigate to published list
                published_tab = page.locator('a:has-text("已发表"), :has-text("已发表")')
                if await published_tab.count() > 0 and await published_tab.first.is_visible():
                    await published_tab.first.click()
                    await asyncio.sleep(1.0)

                # Search for exact title match
                item = page.locator(
                    f'.weui-desktop-mass__item:has-text("{title}"), '
                    f'.publish_item:has-text("{title}"), '
                    f'a:has-text("{title}")'
                )
                if await item.count() > 0:
                    link = item.first.locator('a[href*="/s/"], a[href*="mp.weixin.qq.com/s"]')
                    if await link.count() > 0:
                        href = await link.first.get_attribute("href")
                        if href:
                            full_url = str(href)
                            if full_url.startswith("/s/"):
                                full_url = f"https://mp.weixin.qq.com{full_url}"
                            if full_url.startswith("https://mp.weixin.qq.com/s/"):
                                return full_url
            except Exception as exc:
                logger.debug("reconcile_check_error", error=str(exc))
            await asyncio.sleep(5.0)
        return None
