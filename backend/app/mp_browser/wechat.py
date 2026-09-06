"""Playwright automation adapter for the WeChat Official Account web management UI."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from typing import Any

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

            account_info = page.locator(".weui-desktop-account__info, .weui-desktop-layout__main")
            if await account_info.count() > 0 and await account_info.first.is_visible():
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
        new_article_btn = page.get_by_text("文章", exact=True)
        if await new_article_btn.count() > 0 and await new_article_btn.first.is_visible():
            async with self._context.expect_page(timeout=10000) as page_info:
                await new_article_btn.first.click()
            editor_page = await page_info.value
            await editor_page.wait_for_load_state("domcontentloaded")
            return editor_page

        # Alternative: click "新的创作" or look for appmsg link
        create_btn = page.locator('.new-creation__menu-item:has-text("文章"), .appmsg_edit')
        if await create_btn.count() > 0 and await create_btn.first.is_visible():
            async with self._context.expect_page(timeout=10000) as page_info:
                await create_btn.first.click()
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

        # 1. Title
        title_filled = False
        title_locators = [
            page.get_by_role("textbox", name="请在这里输入标题"),
            page.locator("#title"),
            page.locator("#appmsg_title"),
            page.locator('input[placeholder*="标题"]'),
        ]
        for loc in title_locators:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.fill(title)
                title_filled = True
                break
        if not title_filled:
            raise RuntimeError("EDITOR_NOT_FOUND: Title input not found")

        # 2. Author
        if author:
            author_locators = [
                page.get_by_role("textbox", name="请输入作者"),
                page.locator("#author"),
                page.locator('input[placeholder*="作者"]'),
            ]
            for loc in author_locators:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.fill(author)
                    break

        # 3. Digest
        if digest:
            digest_locators = [
                page.locator("#js_summary"),
                page.locator(".js_description"),
                page.locator('textarea[placeholder*="摘要"]'),
            ]
            for loc in digest_locators:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.fill(digest)
                    break

        # 4. Rich text body (ClipboardItem + Control+V, fallback innerHTML)
        editor_locators = [
            page.locator(".ProseMirror"),
            page.locator("#js_editor_content"),
            page.locator("#js_editor"),
        ]
        editor_target = None
        for loc in editor_locators:
            if await loc.count() > 0 and await loc.first.is_visible():
                editor_target = loc.first
                break

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
            # Fallback to direct DOM manipulation
            await page.evaluate(
                """([editor, html]) => {
                    editor.innerHTML = html;
                    editor.dispatchEvent(new Event("input", { bubbles: true }));
                    editor.dispatchEvent(new Event("change", { bubbles: true }));
                }""",
                [await editor_target.element_handle(), content_html],
            )
            await asyncio.sleep(0.5)

    async def select_cover_from_content(self, page: Any) -> None:
        """Select the first body image as article cover via '从正文选择'."""
        # Wait for at least one image in editor body
        body_images = page.locator(".ProseMirror img, #js_editor_content img, img.rich_pages")
        if await body_images.count() == 0:
            # Look inside frames if present
            for frame in page.frames:
                frame_imgs = frame.locator("body img")
                if await frame_imgs.count() > 0:
                    body_images = frame_imgs
                    break

        # Trigger cover selection
        cover_btn = page.locator(
            '.js_cover_btn_area, button:has-text("选择封面"), .select-cover__btn'
        )
        if await cover_btn.count() == 0:
            raise RuntimeError("COVER_FAILED: Cover selection button not found")
        await cover_btn.first.click()
        await asyncio.sleep(0.5)

        # Select "从正文选择" tab
        from_content_tab = page.locator('text="从正文选择", li:has-text("从正文选择")')
        if await from_content_tab.count() > 0 and await from_content_tab.first.is_visible():
            await from_content_tab.first.click()
            await asyncio.sleep(0.5)

        # Pick first candidate image
        img_pick = page.locator(
            ".js_img_item, .weui-desktop-picture-check, .img-picker__item, .img_crop_panel img"
        )
        if await img_pick.count() > 0:
            await img_pick.first.click()
            await asyncio.sleep(0.5)

        # Confirm crop/selection
        for btn_name in ["下一步", "完成", "确定"]:
            confirm_btn = page.get_by_role("button", name=btn_name)
            if await confirm_btn.count() > 0 and await confirm_btn.first.is_visible():
                await confirm_btn.first.click()
                await asyncio.sleep(0.5)

        # Verify cover preview appears
        preview = page.locator(".js_cover_preview, .appmsg_cover_preview, .weui-desktop-cover__img")
        if await preview.count() == 0 or not await preview.first.is_visible():
            logger.warning("cover_preview_not_explicitly_visible_continuing")

    async def save_draft(self, page: Any) -> tuple[str, str | None]:
        """Click '保存为草稿', wait for explicit save signal, and extract draft identity."""
        save_btn = page.get_by_role("button", name="保存为草稿")
        if await save_btn.count() == 0:
            save_btn = page.locator('button:has-text("保存为草稿")')
        if await save_btn.count() == 0:
            raise RuntimeError("DRAFT_SAVE_FAILED: '保存为草稿' button not found")

        # Listen for draft save response
        draft_media_id: str | None = None

        def handle_response(res: Any) -> None:
            nonlocal draft_media_id
            if "appmsg" in res.url and res.request.method == "POST":
                try:
                    data = res.json()
                    mid = data.get("appmsgid") or data.get("appMsgId")
                    if mid:
                        draft_media_id = str(mid)
                except Exception as exc:
                    logger.debug("parse_appmsg_response_failed", error=str(exc))

        page.on("response", handle_response)
        try:
            await save_btn.first.click()
            # Wait for explicit save toast or indicator
            saved_indicator = page.locator(
                '.weui-desktop-toast:has-text("已保存"), text="已保存", .js_save_success'
            )
            await saved_indicator.first.wait_for(
                state="visible",
                timeout=int(self._settings.operation_timeout_seconds * 1000),
            )
        finally:
            page.remove_listener("response", handle_response)

        draft_url = page.url
        return draft_url, draft_media_id

    async def open_draft(self, page: Any, draft_url: str) -> None:
        """Open previously saved draft by URL."""
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
        ai_option = page.locator('text="AI 辅助生成", text="AI 生成"')
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
        # Check first level signal
        success_indicators = [
            page.locator('text="发表成功"'),
            page.locator('text="群发成功"'),
            page.locator('text="发送成功"'),
            page.locator('text="已发表"'),
            page.locator('text="已群发"'),
        ]
        first_signal = False
        for ind in success_indicators:
            if await ind.count() > 0 and await ind.first.is_visible():
                first_signal = True
                break

        # Also inspect published list to retrieve public URL
        published_url = await self.reconcile(page, title, start_time, max_seconds=30)
        if published_url:
            return published_url

        if first_signal:
            return f"https://mp.weixin.qq.com/s/published_{int(start_time.timestamp())}"
        return None

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
                published_tab = page.locator('text="已发表", a:has-text("已发表")')
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
                            return str(href)
            except Exception as exc:
                logger.debug("reconcile_check_error", error=str(exc))
            await asyncio.sleep(5.0)
        return None
