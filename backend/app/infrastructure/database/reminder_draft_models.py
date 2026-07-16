from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.database.base import Base, StringIdMixin
from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class ReminderDraft(StringIdMixin, Base):
    __tablename__ = "reminder_drafts"
    __table_args__ = (
        Index("ix_reminder_drafts_owner_status", "created_by", "status"),
        Index("ix_reminder_drafts_expiry", "status", "expires_at"),
    )

    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parse_method: Mapped[str] = mapped_column(String(20), nullable=False)
    validation_errors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_reminder_id: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
