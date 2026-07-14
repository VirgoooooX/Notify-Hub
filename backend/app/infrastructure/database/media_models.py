from __future__ import annotations

from datetime import datetime

from app.infrastructure.database.base import Base, StringIdMixin
from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class MediaAsset(StringIdMixin, Base):
    """Metadata for a file whose path is controlled by MediaStorage."""

    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_assets_expires_at", "expires_at"),
        Index("ix_media_assets_checksum", "checksum_sha256"),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_media_id: Mapped[str | None] = mapped_column(String(256))
    provider_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
