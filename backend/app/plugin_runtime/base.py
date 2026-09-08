from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from app.application.time_utils import ensure_utc
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


class PublishVariant(BaseModel):
    platform: Literal["wechat_mp", "xiaohongshu"]
    mode: Literal["draft", "publish"] | None = None
    title: str = Field(min_length=1, max_length=200)
    body_text: str = Field(default="", max_length=100000)
    body_html: str | None = Field(default=None, max_length=500000)
    image_urls: list[AnyHttpUrl] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


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
    publish_to_mp: bool = False
    publish_variants: list[PublishVariant] = Field(default_factory=list)
    recipients: list[str] | None = None
    require_ack: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_an_instant(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value, field="occurred_at")

    @field_validator("recipients")
    @classmethod
    def clean_recipients(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not item.strip() for item in value):
            raise ValueError("recipient ids cannot be empty")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def sync_legacy_publish_to_mp(self) -> EventDraft:
        # Legacy compatibility: if publish_to_mp is True and wechat_mp variant is missing, auto-create it
        has_mp_variant = any(v.platform == "wechat_mp" for v in self.publish_variants)
        if self.publish_to_mp and not has_mp_variant:
            img_list: list[AnyHttpUrl] = []
            if self.article and self.article.image_url:
                img_list.append(self.article.image_url)
            elif self.image_url:
                img_list.append(self.image_url)

            mp_variant = PublishVariant(
                platform="wechat_mp",
                mode=None,
                title=self.article.title if self.article else self.title,
                body_text=self.article.description if self.article else self.content,
                body_html=None,
                image_urls=img_list,
            )
            self.publish_variants.append(mp_variant)

        # Conversely, if wechat_mp variant exists, reflect on legacy flag
        if any(v.platform == "wechat_mp" for v in self.publish_variants):
            self.publish_to_mp = True

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
