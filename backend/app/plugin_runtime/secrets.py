from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{1,99}$")


class SecretRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plugin_id: str
    name: str

    @field_validator("plugin_id", "name")
    @classmethod
    def validate_component(cls, value: str) -> str:
        if not SECRET_NAME.fullmatch(value):
            raise ValueError("secret reference components must be lower snake_case")
        return value

    @classmethod
    def parse(cls, value: str) -> SecretRef:
        parts = value.split("/")
        if len(parts) != 3 or parts[0] != "plugin":
            raise ValueError("secret ref must be plugin/<plugin_id>/<name>")
        return cls(plugin_id=parts[1], name=parts[2])

    def __str__(self) -> str:
        return f"plugin/{self.plugin_id}/{self.name}"
