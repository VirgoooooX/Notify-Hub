"""Configuration settings for the independent MP Playwright browser container."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MPBrowserSettings(BaseSettings):
    """Browser sidecar configuration loaded from NOTIFY_HUB_MP_BROWSER_ environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="NOTIFY_HUB_MP_BROWSER_",
        extra="ignore",
    )

    api_base_url: str = "http://notify-hub:8000/api/v1/admin/mp-browser"
    api_key: SecretStr
    profile_dir: Path = Path("/app/browser-data/profile")
    artifacts_dir: Path = Path("/app/browser-data/artifacts")
    poll_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    heartbeat_seconds: float = Field(default=15.0, ge=3.0, le=60.0)
    navigation_timeout_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    operation_timeout_seconds: float = Field(default=120.0, ge=10.0, le=600.0)
    headless: bool = True

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("api_base_url must use HTTP or HTTPS")
        if not parsed.hostname:
            raise ValueError("api_base_url must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("api_base_url must not include credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("api_base_url must not include query or fragment")
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if not secret.startswith("nfy_"):
            raise ValueError("API key must start with 'nfy_'")
        return SecretStr(secret)

    def ensure_directories(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
