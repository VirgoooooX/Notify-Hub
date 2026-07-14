from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class XPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^\d+$")
    author_username: str
    author_display_name: str | None = None
    text: str
    url: AnyHttpUrl
    published_at: datetime
    is_repost: bool = False
    is_reply: bool = False

    photo_urls: list[AnyHttpUrl] = Field(default_factory=list)
    video_thumbnail_urls: list[AnyHttpUrl] = Field(default_factory=list)
    animated_thumbnail_urls: list[AnyHttpUrl] = Field(default_factory=list)

    quoted_photo_urls: list[AnyHttpUrl] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return value
