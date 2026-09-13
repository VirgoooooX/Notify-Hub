from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.database.base import Base, StringIdMixin, TimestampMixin
from app.infrastructure.database.utc_datetime import UTCDateTime
from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class ApplicationProfile(StringIdMixin, TimestampMixin, Base):
    """Durable metadata and capability flags for one notification namespace."""

    __tablename__ = "application_profiles"
    __table_args__ = (
        UniqueConstraint("key", name="uq_application_profiles_key"),
        Index("ix_application_profiles_enabled", "enabled"),
    )

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WeComProfileConfig(TimestampMixin, Base):
    """Non-secret per-profile WeCom Agent settings."""

    __tablename__ = "wecom_profile_configs"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("application_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[int | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    callback_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProfileMember(StringIdMixin, Base):
    """Membership of an existing Person in an application profile."""

    __tablename__ = "profile_members"
    __table_args__ = (
        UniqueConstraint("profile_id", "person_id", name="uq_profile_members_profile_person"),
        Index("ix_profile_members_enabled", "profile_id", "enabled"),
    )

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("application_profiles.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProfileUserState(StringIdMixin, Base):
    """Profile-local interaction pointer for a WeCom identity."""

    __tablename__ = "profile_user_states"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "wecom_identity_id", name="uq_profile_user_states_profile_identity"
        ),
        Index("ix_profile_user_states_latest", "profile_id", "latest_interactive_occurrence_id"),
    )

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("application_profiles.id", ondelete="CASCADE"), nullable=False
    )
    wecom_identity_id: Mapped[str] = mapped_column(
        ForeignKey("wecom_identities.id", ondelete="CASCADE"), nullable=False
    )
    latest_interactive_occurrence_id: Mapped[str | None] = mapped_column(
        ForeignKey("reminder_occurrences.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MediaProviderRef(StringIdMixin, Base):
    """Per-profile temporary provider media cache entry."""

    __tablename__ = "media_provider_refs"
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "profile_id", "channel", name="uq_media_provider_refs_asset_profile_channel"
        ),
        Index("ix_media_provider_refs_expiry", "expires_at"),
    )

    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("application_profiles.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_media_id: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
