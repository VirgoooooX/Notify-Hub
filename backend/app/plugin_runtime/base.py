from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from app.plugin_runtime.schema import validate_json_schema


class PluginMetadata(BaseModel):
    id: str
    name: str
    version: str


class PluginRunResult(BaseModel):
    status: str = "succeeded"
    emitted_events: int = Field(default=0, ge=0)
    message: str | None = Field(default=None, max_length=500)


class ArticleDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    url: AnyHttpUrl
    image_url: AnyHttpUrl | None = None


class EventDraft(BaseModel):
    event_type: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
    event_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=10000)
    level: Literal["info", "warning", "critical"] = "info"
    occurred_at: datetime | None = None
    url: AnyHttpUrl | None = None
    image_url: AnyHttpUrl | None = None
    message_type: Literal["text", "article"] = "text"
    article: ArticleDraft | None = None
    recipients: list[str] | None = None
    require_ack: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("recipients")
    @classmethod
    def clean_recipients(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not item.strip() for item in value):
            raise ValueError("recipient ids cannot be empty")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def limit_payload(self) -> EventDraft:
        if len(repr(self.payload).encode()) > 65536:
            raise ValueError("payload is too large")
        return self


class EventReceipt(BaseModel):
    event_id: str
    status: Literal["accepted", "duplicate"]


class NotifyPlugin(ABC):
    @classmethod
    @abstractmethod
    def metadata(cls) -> PluginMetadata: ...

    @classmethod
    @abstractmethod
    def config_schema(cls) -> Mapping[str, Any]: ...

    @classmethod
    def validate_config(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(config)
        validate_json_schema(normalized, cls.config_schema())
        return normalized

    async def start(self, context: Any) -> None:
        del context

    @abstractmethod
    async def run(self, context: Any) -> PluginRunResult: ...

    async def stop(self) -> None:
        return None

    async def health_check(self, context: Any) -> dict[str, Any]:
        del context
        return {"healthy": True}
