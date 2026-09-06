"""Tests for MPBrowserWorker, artifact cleaning, error code parsing, and settings."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from app.mp_browser.config import MPBrowserSettings
from app.mp_browser.worker import clean_artifacts, parse_error_code
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
