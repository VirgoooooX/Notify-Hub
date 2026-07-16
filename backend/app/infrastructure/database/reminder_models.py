from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.database.base import Base, StringIdMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class Reminder(StringIdMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_due", "status", "next_run_at"),)

    creator_person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(20), default="text", server_default="text", nullable=False
    )
    media_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    url: Mapped[str | None] = mapped_column(String(2048))
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recurrence_rule: Mapped[str | None] = mapped_column(String(500))
    schedule_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    misfire_policy: Mapped[str] = mapped_column(
        String(20), default="fire_once", server_default="fire_once", nullable=False
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    broadcast: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    notify_on_all_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    require_ack: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ack_policy: Mapped[str] = mapped_column(String(10), nullable=False)
    repeat_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    max_reminders: Mapped[int | None] = mapped_column(Integer)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_stop_after_seconds: Mapped[int | None] = mapped_column(Integer)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReminderRecipient(StringIdMixin, Base):
    __tablename__ = "reminder_recipients"
    __table_args__ = (UniqueConstraint("reminder_id", "person_id", name="uq_reminder_recipient"),)

    reminder_id: Mapped[str] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ReminderOccurrence(StringIdMixin, Base):
    __tablename__ = "reminder_occurrences"
    __table_args__ = (
        UniqueConstraint("reminder_id", "occurrence_key", name="uq_reminder_occurrence_key"),
        Index("ix_reminder_occurrences_due", "status", "expires_at"),
        Index(
            "ix_occurrence_broadcast_work",
            "broadcast_snapshot",
            "status",
            "broadcast_sent_at",
        ),
    )

    reminder_id: Mapped[str] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_key: Mapped[str] = mapped_column(String(200), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    broadcast_snapshot: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    notify_on_all_completed_snapshot: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    broadcast_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broadcast_completion_announced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    broadcast_claimed_by: Mapped[str | None] = mapped_column(String(100))
    broadcast_claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    content_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_type_snapshot: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    media_asset_id_snapshot: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(2048))
    ack_policy_snapshot: Mapped[str] = mapped_column(String(10), nullable=False)
    repeat_interval_seconds_snapshot: Mapped[int | None] = mapped_column(Integer)
    max_reminders_snapshot: Mapped[int | None] = mapped_column(Integer)
    stop_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReminderOccurrenceRecipient(StringIdMixin, Base):
    __tablename__ = "reminder_occurrence_recipients"
    __table_args__ = (
        UniqueConstraint("occurrence_id", "person_id", name="uq_occurrence_recipient"),
        Index("ix_occurrence_recipients_due", "status", "next_notify_at"),
    )

    occurrence_id: Mapped[str] = mapped_column(
        ForeignKey("reminder_occurrences.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    notify_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_notify_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationSession(StringIdMixin, Base):
    __tablename__ = "conversation_sessions"

    wecom_identity_id: Mapped[str] = mapped_column(
        ForeignKey("wecom_identities.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("reminder_drafts.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IncomingMessage(StringIdMixin, Base):
    __tablename__ = "incoming_messages"
    __table_args__ = (
        UniqueConstraint("channel", "dedupe_key", name="uq_incoming_message_dedupe"),
        Index("ix_incoming_processing", "processing_status", "received_at"),
    )

    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    sender_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    media_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500))


class NotificationAction(StringIdMixin, Base):
    __tablename__ = "notification_actions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_notification_action_token_hash"),
        Index("ix_notification_actions_reminder", "reminder_id", "recipient_id"),
    )

    reminder_id: Mapped[str] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[str] = mapped_column(
        ForeignKey("reminder_recipients.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_id: Mapped[str | None] = mapped_column(
        ForeignKey("reminder_occurrences.id", ondelete="CASCADE")
    )
    occurrence_recipient_id: Mapped[str | None] = mapped_column(
        ForeignKey("reminder_occurrence_recipients.id", ondelete="CASCADE")
    )
    notification_id: Mapped[str | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InteractionEvent(StringIdMixin, Base):
    __tablename__ = "interaction_events"
    __table_args__ = (
        UniqueConstraint("channel", "dedupe_key", name="uq_interaction_event_dedupe"),
    )

    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    notification_action_id: Mapped[str | None] = mapped_column(
        ForeignKey("notification_actions.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[str | None] = mapped_column(String(100))
    response_code: Mapped[str | None] = mapped_column(String(500))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(200))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
