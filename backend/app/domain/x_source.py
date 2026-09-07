from __future__ import annotations

from datetime import datetime
from typing import Any

from app.application.time_utils import ensure_utc
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class XPostRecord(BaseModel):
    """Provider-neutral X post returned by the platform source capability."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^\d+$", min_length=1, max_length=32)
    author_username: str = Field(min_length=1, max_length=64)
    author_display_name: str | None = Field(default=None, max_length=200)
    text: str = Field(default="", max_length=20_000)
    url: AnyHttpUrl
    published_at: datetime
    is_repost: bool = False
    is_reply: bool = False
    photo_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=20)
    video_thumbnail_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=20)
    animated_thumbnail_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=20)
    quoted_photo_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=20)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: datetime) -> datetime:
        return ensure_utc(value, field="published_at")


class XSourceError(RuntimeError):
    """Stable base error for the X source capability."""

    code = "x_source_error"
    retryable = True


class XSourceUnavailable(XSourceError):
    code = "x_source_unavailable"


class XSourceCookieInvalid(XSourceError):
    """The configured X Cookie was rejected or is not usable."""

    code = "x_source_cookie_invalid"
    retryable = False
    alert_immediately = True


class XTwscrapeIncompatible(XSourceError):
    """The installed twscrape version no longer matches X's web client."""

    code = "x_twscrape_incompatible"
    alert_immediately = True


class XTwscrapeUnavailable(XSourceUnavailable):
    """twscrape could not complete a request for a transient reason."""

    code = "x_twscrape_unavailable"


class XSourcesUnavailable(XSourceUnavailable):
    """Both the configured primary source and the RSSHub fallback failed."""

    code = "x_sources_unavailable"
    alert_immediately = True

    def __init__(self, primary: XSourceError, fallback: XSourceError) -> None:
        self.primary_code = primary.code
        self.fallback_code = fallback.code
        super().__init__(
            f"primary X source failed ({primary.code}); RSSHub fallback failed ({fallback.code})"
        )


class XSourceRateLimited(XSourceError):
    code = "x_source_rate_limited"


class XSourceParseError(XSourceError):
    code = "x_source_parse_error"
    retryable = False


class XSourceAccountError(XSourceError):
    code = "x_source_account_error"
    retryable = False
