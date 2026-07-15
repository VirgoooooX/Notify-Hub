from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.database.base import Base, StringIdMixin, TimestampMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class AIProvider(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "ai_providers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    preset: Mapped[str] = mapped_column(String(40), nullable=False)
    protocol: Mapped[str] = mapped_column(String(40), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_private_network: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    structured_output_mode: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    custom_headers_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    custom_query: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIProviderModel(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "ai_provider_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_ai_provider_model"),
        Index("ix_ai_provider_models_allowed", "provider_id", "available", "enabled"),
    )

    provider_id: Mapped[str] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(300), nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AIProfile(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "ai_profiles"
    __table_args__ = (Index("ix_ai_profiles_provider", "provider_id", "enabled"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    capability: Mapped[str] = mapped_column(String(20), default="classify", nullable=False)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="RESTRICT"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(300), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=160, nullable=False)
    response_format: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    output_language: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(
        String(30), default="provider_default", nullable=False
    )
    verbosity: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)
    include_reason: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_reason_characters: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    system_instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=2592000, nullable=False)
    daily_request_limit: Mapped[int | None] = mapped_column(Integer)
    daily_token_limit: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIResponseCache(StringIdMixin, Base):
    __tablename__ = "ai_response_cache"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "profile_revision",
            "prompt_version",
            "input_hash",
            name="uq_ai_cache_input",
        ),
        Index("ix_ai_response_cache_expires", "expires_at"),
    )

    cache_key: Mapped[str | None] = mapped_column(String(500))
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("ai_profiles.id", ondelete="CASCADE"), nullable=False
    )
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIInvocation(StringIdMixin, Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (
        Index("ix_ai_invocations_profile_created", "profile_id", "created_at"),
        Index("ix_ai_invocations_plugin_created", "plugin_id", "created_at"),
    )

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("ai_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    plugin_id: Mapped[str | None] = mapped_column(String(64))
    plugin_run_id: Mapped[str | None] = mapped_column(String(64))
    use_case: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
