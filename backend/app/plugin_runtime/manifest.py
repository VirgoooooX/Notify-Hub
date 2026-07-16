from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
CRON_RE = re.compile(r"^[\d*/?,\-]+(?:\s+[\d*/?,\-]+){4}$")


class IntervalSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["interval"]
    seconds: int = Field(ge=60, le=86400 * 30)


class CronSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["cron"]
    expression: str
    timezone: str = "UTC"

    @field_validator("expression")
    @classmethod
    def validate_expression_shape(cls, value: str) -> str:
        value = " ".join(value.split())
        if not CRON_RE.fullmatch(value):
            raise ValueError("cron expression must contain five supported fields")
        return value


PluginSchedule = IntervalSchedule | CronSchedule


class PluginReminderPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    create: bool = False
    allow_recurring: bool = False
    allow_cron: bool = False
    allow_interactive: bool = False
    allow_media: bool = False
    allowed_recipients: list[str] = Field(default_factory=list, max_length=100)
    max_active: int = Field(default=10, ge=1, le=1000)
    min_interval_seconds: int = Field(default=300, ge=300, le=86400 * 30)
    max_duration_seconds: int = Field(default=86400, ge=300, le=86400 * 366)
    max_notifications: int = Field(default=12, ge=1, le=100)

    @field_validator("allowed_recipients")
    @classmethod
    def unique_recipients(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reminder recipients cannot be empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("reminder recipients must be unique")
        return cleaned

    @model_validator(mode="after")
    def coherent_permissions(self) -> PluginReminderPermissions:
        if not self.create and any(
            (self.allow_recurring, self.allow_cron, self.allow_interactive, self.allow_media)
        ):
            raise ValueError("reminder capabilities require create=true")
        if self.allow_cron and not self.allow_recurring:
            raise ValueError("cron permission requires allow_recurring=true")
        if self.create and not self.allowed_recipients:
            raise ValueError("reminder creation requires an explicit recipient allowlist")
        return self


class PluginPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    broadcast: bool = False
    media_write: bool = False
    private_network: list[str] = Field(default_factory=list)
    ai_profiles: list[str] = Field(default_factory=list)
    ai_capabilities: list[Literal["classify", "extract", "summarize"]] = Field(default_factory=list)
    reminders: PluginReminderPermissions = Field(default_factory=PluginReminderPermissions)

    @field_validator("network", "secrets", "private_network", "ai_profiles", "ai_capabilities")
    @classmethod
    def unique_nonempty_values(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("permission entries cannot be empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("permission entries must be unique")
        return cleaned


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str = Field(min_length=1, max_length=200)
    version: str
    description: str = Field(default="", max_length=1000)
    entrypoint: str
    api_version: Literal["1"]
    kind: Literal["monitor", "integration", "system"] = "monitor"
    trusted: bool
    default_schedule: PluginSchedule = Field(discriminator="type")
    max_concurrency: int = Field(default=1, ge=1, le=8)
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600)
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not PLUGIN_ID_RE.fullmatch(value):
            raise ValueError("plugin id must be lower snake_case")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not SEMVER_RE.fullmatch(value):
            raise ValueError("plugin version must be semantic versioning")
        return value

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(parts):
            raise ValueError("entrypoint must be module:Class")
        module, class_name = parts
        if (
            not all(part.isidentifier() for part in module.split("."))
            or not class_name.isidentifier()
        ):
            raise ValueError("entrypoint contains an invalid identifier")
        return value

    @model_validator(mode="after")
    def trusted_only(self) -> PluginManifest:
        if not self.trusted:
            raise ValueError("in-process plugins must be trusted")
        if self.max_concurrency != 1:
            raise ValueError("plugin API v1 requires max_concurrency=1")
        return self
