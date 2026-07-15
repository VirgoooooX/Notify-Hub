from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
