from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_WECOM_API_BASE_URL = "https://qyapi.weixin.qq.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NOTIFY_HUB_", extra="ignore", case_sensitive=False
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./data/notify-hub.db"
    app_timezone: str = "Asia/Shanghai"
    public_base_url: str | None = None
    log_level: str = "INFO"
    log_dir: Path = Path("./logs")
    jwt_secret: SecretStr = Field(default=SecretStr("development-only-change-me"))
    secret_encryption_key: SecretStr | None = None
    access_token_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_days: int = Field(default=30, ge=1, le=365)
    login_max_attempts: int = Field(default=5, ge=1, le=100)
    login_window_seconds: int = Field(default=300, ge=1)
    api_rate_limit_per_minute: int = Field(default=60, ge=1)
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=100)
    worker_heartbeat_ttl_seconds: int = Field(default=60, ge=5)
    delivery_lease_seconds: int = Field(default=120, ge=5)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    reminder_poll_seconds: float = Field(default=5.0, gt=0, le=60)
    wecom_corp_id: str | None = None
    wecom_agent_id: int | None = None
    wecom_secret: SecretStr | None = None
    wecom_api_base_url: str = DEFAULT_WECOM_API_BASE_URL
    wecom_request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    wecom_token_refresh_skew_seconds: int = Field(default=120, ge=0)
    wecom_callback_token: SecretStr | None = None
    wecom_callback_aes_key: SecretStr | None = None
    wecom_callback_replay_window_seconds: int = Field(default=300, ge=30, le=3600)
    wecom_callback_max_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    allow_broadcast: bool = False
    media_root: Path = Path("./data/media")
    media_image_max_bytes: int = Field(default=2_097_152, gt=5, le=2_097_152)
    media_voice_max_bytes: int = Field(default=2_097_152, gt=5, le=2_097_152)
    media_voice_max_seconds: float = Field(default=60.0, gt=0, le=60)
    media_download_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    media_download_max_redirects: int = Field(default=3, ge=0, le=5)
    media_retention_seconds: int = Field(default=2_592_000, ge=60, le=2_592_000)
    asr_command: list[str] = Field(default_factory=list)
    tts_command: list[str] = Field(default_factory=list)
    transcode_command: list[str] = Field(default_factory=list)

    @field_validator("database_url")
    @classmethod
    def ensure_async_database(cls, value: str) -> str:
        if value.startswith("sqlite:///"):
            return value.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return value

    @field_validator("wecom_api_base_url")
    @classmethod
    def validate_wecom_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https":
            raise ValueError("WECOM API base URL must use HTTPS")
        if not parsed.hostname:
            raise ValueError("WECOM API base URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("WECOM API base URL must not include credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("WECOM API base URL must not include a query or fragment")
        return value.rstrip("/")

    @field_validator("wecom_agent_id", mode="before")
    @classmethod
    def blank_wecom_agent_id_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def production_secrets_are_strong(self) -> "Settings":
        if self.environment == "production":
            jwt = self.jwt_secret.get_secret_value()
            if len(jwt) < 32 or jwt == "development-only-change-me":
                raise ValueError("production JWT secret must be at least 32 characters")
            encryption_key = (
                self.secret_encryption_key.get_secret_value()
                if self.secret_encryption_key is not None
                else ""
            )
            if len(encryption_key) < 32:
                raise ValueError("production secret encryption key must be at least 32 characters")

        outbound_wecom = (
            bool(self.wecom_corp_id and self.wecom_corp_id.strip()),
            self.wecom_agent_id is not None,
            bool(self.wecom_secret and self.wecom_secret.get_secret_value()),
        )
        if any(outbound_wecom) and not all(outbound_wecom):
            raise ValueError("WeCom Corp ID, Agent ID, and Secret must be configured together")

        callback_wecom = (
            bool(self.wecom_callback_token and self.wecom_callback_token.get_secret_value()),
            bool(self.wecom_callback_aes_key and self.wecom_callback_aes_key.get_secret_value()),
        )
        if any(callback_wecom) and not all(callback_wecom):
            raise ValueError("WeCom callback Token and AES key must be configured together")
        return self

    def ensure_sqlite_parent(self) -> None:
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix) and ":memory:" not in self.database_url:
            Path(self.database_url.removeprefix(prefix)).parent.mkdir(parents=True, exist_ok=True)
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
