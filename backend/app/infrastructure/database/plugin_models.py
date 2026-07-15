from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.database.base import Base, StringIdMixin, TimestampMixin
from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class PluginRecord(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "plugins"
    __table_args__ = (Index("ix_plugins_schedule", "enabled", "next_run_at"),)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    install_type: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="disabled", nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    circuit_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schedule_inherits_default: Mapped[bool | None] = mapped_column(
        Boolean, default=True, nullable=True
    )


class PluginConfig(Base):
    __tablename__ = "plugin_configs"
    plugin_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PluginState(Base):
    __tablename__ = "plugin_states"
    plugin_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PluginRun(StringIdMixin, Base):
    __tablename__ = "plugin_runs"
    __table_args__ = (
        Index("ix_plugin_runs_queue", "status", "created_at"),
        Index("ix_plugin_runs_history", "plugin_id", "started_at"),
    )
    plugin_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    emitted_event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cursor_before: Mapped[Any | None] = mapped_column(JSON)
    cursor_after: Mapped[Any | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(500))
    trace_id: Mapped[str | None] = mapped_column(String(100))
    worker_id: Mapped[str | None] = mapped_column(String(100))
