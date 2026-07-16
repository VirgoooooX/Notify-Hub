from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator


class DataResponse(BaseModel):
    data: Any
    request_id: str


class AuthInitialize(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=256)


class AuthLogin(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class ApiClientCreate(BaseModel):
    id: str | None = Field(default=None, pattern=r"^client_[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    allowed_event_types: list[str] = Field(default_factory=list)
    allowed_recipient_ids: list[str] = Field(default_factory=list)
    allow_broadcast: bool = False
    allow_critical: bool = False
    allow_media: bool = False
    allow_voice: bool = False
    allow_reminders: bool = False
    allow_recurring: bool = False
    allow_cron: bool = False
    allow_interactive: bool = False
    max_active_reminders: int = Field(default=10, ge=1, le=1000)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)


class ApiClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    allowed_event_types: list[str] | None = None
    allowed_recipient_ids: list[str] | None = None
    allow_broadcast: bool | None = None
    allow_critical: bool | None = None
    allow_media: bool | None = None
    allow_voice: bool | None = None
    allow_reminders: bool | None = None
    allow_recurring: bool | None = None
    allow_cron: bool | None = None
    allow_interactive: bool | None = None
    max_active_reminders: int | None = Field(default=None, ge=1, le=1000)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10000)


class PersonCreate(BaseModel):
    id: str | None = Field(default=None, pattern=r"^person_[A-Za-z0-9_-]+$")
    display_name: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("display_name", "name"),
    )
    is_default: bool = False


class PersonUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    is_default: bool | None = None


class IdentityCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


class EventCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    event_type: str = Field(min_length=1, max_length=100)
    event_key: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=20000)
    level: Literal["info", "warning", "critical"] = "info"
    occurred_at: datetime | None = None
    url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    recipients: list[str] = Field(min_length=1, max_length=100)
    broadcast: bool = False
    message_type: Literal["text", "article", "image", "voice"] = "text"
    media_asset_id: str | None = Field(default=None, max_length=64)
    require_ack: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def meaningful_content(self) -> "EventCreate":
        if not self.title and not self.content:
            raise ValueError("title and content cannot both be empty")
        if self.broadcast and self.recipients != ["@all"]:
            raise ValueError("broadcast must use the sole recipient @all")
        if not self.broadcast and "@all" in self.recipients:
            raise ValueError("@all requires broadcast=true")
        if len(self.model_dump_json()) > 64 * 1024:
            raise ValueError("event payload exceeds 64 KiB")
        return self


class NotificationCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=20000)
    message_type: Literal["text", "article", "image", "voice"] = "text"
    media_asset_id: str | None = Field(default=None, max_length=64)
    recipients: list[str] = Field(min_length=1)
    priority: Literal["normal", "high", "critical"] = "normal"
    url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    require_ack: bool = False


class WeComTestRequest(BaseModel):
    recipient_id: str
    message_type: Literal["text", "article"] = "text"
