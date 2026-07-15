from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

STATE_KEY = "monitor_state"


class FabrizioHwgConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    username: str = "FabrizioRomano"
    source: Literal["twscrape"] = "twscrape"
    twscrape_fetch_limit: int = Field(default=40, ge=10, le=100)
    first_run_mode: Literal["baseline", "scan_recent"] = "baseline"
    scan_recent_limit: int = Field(default=10, ge=1, le=100)
    recipients: list[str] = Field(default_factory=list)
    notification_level: Literal["info", "warning", "critical"] = "info"
    include_reposts: bool = False
    include_replies: bool = False
    original_posts_only: bool = True
    fallback_cover_url: AnyHttpUrl | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lstrip("@")
        if not normalized or len(normalized) > 64:
            raise ValueError("username must contain 1 to 64 characters")
        return normalized

    @field_validator("recipients")
    @classmethod
    def normalize_recipients(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def validate_original_only(self) -> FabrizioHwgConfig:
        if self.original_posts_only:
            self.include_replies = False
            self.include_reposts = False
        return self


class ArticleDraft(BaseModel):
    title: str
    description: str
    url: AnyHttpUrl
    image_url: AnyHttpUrl | None = None


class EventDraft(BaseModel):
    event_type: str
    event_key: str
    title: str
    content: str
    level: Literal["info", "warning", "critical"] = "info"
    occurred_at: datetime | None = None
    url: AnyHttpUrl | None = None
    image_url: AnyHttpUrl | None = None
    recipients: list[str] | None = None
    require_ack: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    article: ArticleDraft | None = None


class MonitorState(BaseModel):
    schema_version: Literal[1] = 1
    last_seen_post_id: str | None = None
    last_seen_published_at: datetime | None = None
    recent_processed_ids: list[str] = Field(default_factory=list)
    last_success_at: datetime | None = None
    last_source: Literal["twscrape"] | None = None


class PluginRunResult(BaseModel):
    status: Literal["disabled", "baseline_initialized", "success"]
    emitted_events: int = 0
    fetched_posts: int = 0
    new_posts: int = 0
    matched_posts: int = 0
    message: str | None = None


@runtime_checkable
class HttpResponse(Protocol):
    status_code: int
    text: str
    headers: Mapping[str, str]

    def json(self) -> Any: ...


@runtime_checkable
class RestrictedHttpClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,  # noqa: ASYNC109 - mirrors the runtime HTTP client contract.
    ) -> HttpResponse: ...


@runtime_checkable
class PluginMediaPublisher(Protocol):
    async def publish_image_url(
        self,
        source_url: str,
        *,
        retention_seconds: int | None = None,
    ) -> str: ...


@runtime_checkable
class PluginContext(Protocol):
    http: RestrictedHttpClient
    media: PluginMediaPublisher
    logger: Any

    async def get_config(self) -> Mapping[str, Any]: ...
    async def get_state(self, key: str, default: Any = None) -> Any: ...
    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int: ...
    async def get_secret(self, name: str) -> str: ...
    async def emit_event(self, event: EventDraft) -> Any: ...
