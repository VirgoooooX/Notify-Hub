from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from app.infrastructure.database.base import Base, StringIdMixin, TimestampMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


class EventSource(str, enum.Enum):
    API_CLIENT = "api_client"
    PLUGIN = "plugin"
    REMINDER = "reminder"
    SYSTEM = "system"


class EventStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    ROUTED = "routed"
    IGNORED = "ignored"
    FAILED = "failed"


class Level(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MessageType(str, enum.Enum):
    TEXT = "text"
    ARTICLE = "article"
    IMAGE = "image"
    VOICE = "voice"
    TEMPLATE_CARD = "template_card"


class Priority(str, enum.Enum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    DEAD = "dead"
    CANCELLED = "cancelled"


class RecipientType(str, enum.Enum):
    PERSON = "person"
    BROADCAST = "broadcast"


class AttemptStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


class Person(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "people"
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    identities: Mapped[list[WeComIdentity]] = relationship(back_populates="person")


class WeComIdentity(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "wecom_identities"
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latest_interactive_occurrence_id: Mapped[str | None] = mapped_column(
        ForeignKey("reminder_occurrences.id", ondelete="SET NULL")
    )
    person: Mapped[Person] = relationship(back_populates="identities")


class Admin(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "admins"
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    singleton_key: Mapped[str] = mapped_column(
        String(20), unique=True, default="primary", nullable=False
    )


class RefreshSession(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "refresh_sessions"
    admin_id: Mapped[str] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiClient(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "api_clients"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    allowed_event_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_recipient_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allow_broadcast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_voice: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_reminders: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_recurring: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_cron: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_interactive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_active_reminders: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(StringIdMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "event_key", name="uq_event_source_key"),
        Index("ix_events_accepted_at", "accepted_at"),
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    level: Mapped[str] = mapped_column(String(20), default=Level.INFO.value, nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.ACCEPTED.value)
    ignore_reason: Mapped[str | None] = mapped_column(String(500))
    notifications: Mapped[list[Notification]] = relationship(back_populates="event")


class Notification(StringIdMixin, Base):
    __tablename__ = "notifications"
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    reminder_id: Mapped[str | None] = mapped_column(String(64))
    reminder_occurrence_id: Mapped[str | None] = mapped_column(
        ForeignKey("reminder_occurrences.id", ondelete="SET NULL")
    )
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    media_asset_id: Mapped[str | None] = mapped_column(String(64))
    ack_policy: Mapped[str | None] = mapped_column(String(10))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default=Priority.NORMAL.value)
    require_ack: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event: Mapped[Event | None] = relationship(back_populates="notifications")
    deliveries: Mapped[list[Delivery]] = relationship(back_populates="notification")


class Delivery(StringIdMixin, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "channel",
            "recipient_type",
            "recipient_id",
            name="uq_delivery_target",
        ),
        Index("ix_deliveries_queue", "status", "next_attempt_at"),
    )
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(30), default="wecom", nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default=DeliveryStatus.PENDING.value)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notification: Mapped[Notification] = relationship(back_populates="deliveries")
    attempts: Mapped[list[DeliveryAttempt]] = relationship(back_populates="delivery")


class DeliveryAttempt(StringIdMixin, Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (UniqueConstraint("delivery_id", "attempt_no", name="uq_attempt_number"),)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(500))
    provider_status: Mapped[int | None] = mapped_column(Integer)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    delivery: Mapped[Delivery] = relationship(back_populates="attempts")


class Secret(StringIdMixin, TimestampMixin, Base):
    __tablename__ = "secrets"
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    __table_args__ = (UniqueConstraint("scope_type", "scope_id", "name", name="uq_secret_scope"),)


class PlatformSetting(TimestampMixin, Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)


class AuditLog(StringIdMixin, Base):
    __tablename__ = "audit_logs"
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    worker_type: Mapped[str] = mapped_column(String(50), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
