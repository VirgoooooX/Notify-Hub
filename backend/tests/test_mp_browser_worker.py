"""Tests for MPBrowserWorker, artifact cleaning, error code parsing, and settings."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from app.mp_browser.config import MPBrowserSettings
from app.mp_browser.wechat import WeChatPublisher
from app.mp_browser.worker import MPBrowserWorker, clean_artifacts, parse_error_code
from pydantic import SecretStr


def test_clean_artifacts_preserves_most_recent_20(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    # Create 25 dummy screenshot files with spaced timestamps
    created_files: list[Path] = []
    base_time = time.time() - 1000
    for i in range(25):
        f = artifacts_dir / f"article_{i}.png"
        f.write_text(f"dummy content {i}")
        # Set mtime
        os.utime(f, (base_time + i * 10, base_time + i * 10))
        created_files.append(f)

    clean_artifacts(artifacts_dir)

    remaining = list(artifacts_dir.glob("*.png"))
    assert len(remaining) == 20
    # Oldest 5 (article_0 to article_4) should have been deleted
    for i in range(5):
        assert not (artifacts_dir / f"article_{i}.png").exists()
    # Most recent 20 (article_5 to article_24) should still exist
    for i in range(5, 25):
        assert (artifacts_dir / f"article_{i}.png").exists()


def test_parse_error_code_mapping() -> None:
    assert parse_error_code(RuntimeError("COVER_FAILED: Missing picture")) == "COVER_FAILED"
    assert parse_error_code(RuntimeError("AUTH_REQUIRED: Login expired")) == "AUTH_REQUIRED"
    assert parse_error_code(RuntimeError("DRAFT_SAVE_FAILED")) == "DRAFT_SAVE_FAILED"
    assert parse_error_code(TimeoutError("Waiting for selector timed out")) == "EDITOR_TIMEOUT"
    assert parse_error_code(ConnectionError("HTTP connection reset by peer")) == "NETWORK_ERROR"
    assert parse_error_code(ValueError("Unexpected DOM structure")) == "PROVIDER_UI_CHANGED"


def test_mp_browser_settings_validation(tmp_path: Path) -> None:
    # Valid settings
    settings = MPBrowserSettings(
        api_base_url="https://hub.example.com/api/v1/admin/mp-browser",
        api_key=SecretStr("nfy_secure_key_1234567890"),
        profile_dir=tmp_path / "profile",
        artifacts_dir=tmp_path / "artifacts",
    )
    assert settings.api_base_url == "https://hub.example.com/api/v1/admin/mp-browser"
    settings.ensure_directories()
    assert (tmp_path / "profile").exists()
    assert (tmp_path / "artifacts").exists()

    # Invalid key prefix
    with pytest.raises(ValueError, match="API key must start with 'nfy_'"):
        MPBrowserSettings(
            api_key=SecretStr("invalid_prefix_key"),
        )

    # Invalid URL scheme
    with pytest.raises(ValueError, match="must use HTTP or HTTPS"):
        MPBrowserSettings(
            api_base_url="ftp://hub.example.com",
            api_key=SecretStr("nfy_valid_key"),
        )


@pytest.mark.asyncio
async def test_worker_draft_only_mode(tmp_path: Path) -> None:
    settings = MPBrowserSettings(
        api_base_url="https://hub.example.com/api/v1/admin/mp-browser",
        api_key=SecretStr("nfy_secure_key_1234567890"),
        profile_dir=tmp_path / "profile",
        artifacts_dir=tmp_path / "artifacts",
        draft_only=True,
    )
    api_client = AsyncMock()
    publisher = AsyncMock()
    editor_page = MagicMock()
    publisher.open_editor.return_value = editor_page
    publisher.save_draft.return_value = (
        "https://mp.weixin.qq.com/cgi-bin/appmsg?id=123",
        "draft_123",
    )

    worker = MPBrowserWorker(settings, api_client, publisher)
    article = {
        "id": "art_1",
        "title": "Draft Only Title",
        "author": "Notify Hub",
        "digest": "Digest",
        "content": "Content",
        "content_html": "<p>Content</p>",
    }

    dummy_page = MagicMock()
    await worker._process_article(dummy_page, article, resume="new", draft_url=None)

    publisher.fill_article.assert_awaited_once_with(editor_page, article)
    publisher.select_cover_from_content.assert_awaited_once_with(editor_page)
    publisher.save_draft.assert_awaited_once_with(editor_page)

    api_client.checkpoint.assert_awaited_once_with(
        "art_1",
        phase="draft_saved",
        draft_url="https://mp.weixin.qq.com/cgi-bin/appmsg?id=123",
        provider_draft_id="draft_123",
    )

    api_client.complete.assert_awaited_once_with(
        "art_1",
        status="draft",
        provider_draft_id="draft_123",
        published_url="https://mp.weixin.qq.com/cgi-bin/appmsg?id=123",
    )

    publisher.click_publish_and_confirm.assert_not_called()
    publisher.verify_published.assert_not_called()


@pytest.mark.asyncio
async def test_worker_full_publish_mode(tmp_path: Path) -> None:
    settings = MPBrowserSettings(
        api_base_url="https://hub.example.com/api/v1/admin/mp-browser",
        api_key=SecretStr("nfy_secure_key_1234567890"),
        profile_dir=tmp_path / "profile",
        artifacts_dir=tmp_path / "artifacts",
        draft_only=False,
    )
    api_client = AsyncMock()
    publisher = AsyncMock()
    editor_page = MagicMock()
    publisher.open_editor.return_value = editor_page
    publisher.save_draft.return_value = (
        "https://mp.weixin.qq.com/cgi-bin/appmsg?id=123",
        "draft_123",
    )
    publisher.verify_published.return_value = "https://mp.weixin.qq.com/s/pub_123"

    worker = MPBrowserWorker(settings, api_client, publisher)
    article = {
        "id": "art_2",
        "title": "Full Publish Title",
        "author": "Notify Hub",
        "digest": "Digest",
        "content": "Content",
        "content_html": "<p>Content</p>",
    }

    dummy_page = MagicMock()
    await worker._process_article(dummy_page, article, resume="new", draft_url=None)

    assert api_client.checkpoint.await_count == 3
    api_client.checkpoint.assert_has_awaits(
        [
            call(
                "art_2",
                phase="draft_saved",
                draft_url="https://mp.weixin.qq.com/cgi-bin/appmsg?id=123",
                provider_draft_id="draft_123",
            ),
            call("art_2", phase="publish_intent"),
            call("art_2", phase="publish_clicked"),
        ]
    )

    publisher.click_publish_and_confirm.assert_awaited_once_with(editor_page)
    publisher.verify_published.assert_awaited_once()
    api_client.complete.assert_awaited_once_with(
        "art_2",
        provider_draft_id="draft_123",
        published_url="https://mp.weixin.qq.com/s/pub_123",
    )


@pytest.mark.asyncio
async def test_wechat_open_draft_domain_validation(tmp_path: Path) -> None:
    settings = MPBrowserSettings(
        api_base_url="https://hub.example.com/api/v1/admin/mp-browser",
        api_key=SecretStr("nfy_secure_key_1234567890"),
        profile_dir=tmp_path / "profile",
        artifacts_dir=tmp_path / "artifacts",
    )
    publisher = WeChatPublisher(settings)
    mock_page = AsyncMock()

    with pytest.raises(ValueError, match="Invalid draft URL domain or scheme"):
        await publisher.open_draft(mock_page, "http://mp.weixin.qq.com/draft/1")

    with pytest.raises(ValueError, match="Invalid draft URL domain or scheme"):
        await publisher.open_draft(mock_page, "https://attacker.com/steal")

    await publisher.open_draft(mock_page, "https://mp.weixin.qq.com/cgi-bin/appmsg?id=1")
    mock_page.goto.assert_awaited_once()
