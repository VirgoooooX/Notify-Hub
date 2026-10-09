from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AIHubParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=100000)
    reasoning_effort: str | None = Field(default=None, max_length=30)
    timeout_seconds: float | None = Field(default=None, gt=0, le=1800)


class AIHubProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=1000)
    purpose: str = Field(default="", max_length=40)
    enabled: bool
    available: bool
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol: str
    models: list[str]
    parameters: AIHubParameters
    response_format: str = Field(pattern=r"^(auto|json_schema|json_object|prompt_json)$")
    output_language: str = Field(pattern=r"^(auto|zh-CN|en)$")
    verbosity: str = Field(pattern=r"^(concise|standard|detailed)$")
    include_reason: bool
    max_reason_characters: int = Field(ge=0, le=1000)
    cache_ttl_seconds: int = Field(ge=0, le=31536000)
    daily_request_limit: int | None = Field(default=None, ge=1, le=1000000)
    daily_token_limit: int | None = Field(default=None, ge=1, le=1000000000)


class AIClassificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50000)
    cache_key: str | None = Field(default=None, max_length=500)


class AIClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=1000)


class AIClassificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[AIClassificationResult] = Field(min_length=1, max_length=5)

    @field_validator("results")
    @classmethod
    def unique_ids(cls, value: list[AIClassificationResult]) -> list[AIClassificationResult]:
        if len({item.id for item in value}) != len(value):
            raise ValueError("classification result ids must be unique")
        return value


type AIExtractedValue = str | int | float | bool | None


class AIExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    values: dict[str, AIExtractedValue]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=1000)


class AISummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=20000)
    key_points: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("key_points")
    @classmethod
    def non_empty_key_points(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("summary key points must be non-empty and bounded")
        return value
