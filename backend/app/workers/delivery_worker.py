import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import structlog
from app.channels.base import ChannelMessage, ChannelResult, NotificationChannel
from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import (
    AttemptStatus,
    Delivery,
    DeliveryAttempt,
    DeliveryStatus,
    Notification,
    RecipientType,
    WeComIdentity,
    WorkerHeartbeat,
)
from app.infrastructure.database.reminder_models import (
    Reminder,
    ReminderOccurrenceRecipient,
    ReminderRecipient,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

BACKOFF_SECONDS = (30, 120, 600, 3600)
logger = structlog.get_logger()


class DeliveryWorker:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        channel: NotificationChannel,
        clock: Clock,
        worker_id: str,
        lease_seconds: int = 120,
        prepare_tts: Callable[[str], Awaitable[str]] | None = None,
        action_token_for_id: Callable[[str], str] | None = None,
    ) -> None:
        self._factory, self._channel, self._clock = factory, channel, clock
        self.worker_id, self._lease_seconds = worker_id, lease_seconds
        self._prepare_tts = prepare_tts
        self._action_token_for_id = action_token_for_id

    async def run(self, stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
        while not stop.is_set():
            try:
                await self.heartbeat()
                await self.reclaim_expired()
                processed = await self.process_one()
            except Exception as exc:
                logger.exception(
                    "delivery_worker_iteration_failed",
                    worker_id=self.worker_id,
                    error_type=type(exc).__name__,
                )
                processed = False
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)

    async def heartbeat(self) -> None:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            record = await session.get(WorkerHeartbeat, self.worker_id)
            if record is None:
                session.add(
                    WorkerHeartbeat(
                        worker_id=self.worker_id, worker_type="delivery", heartbeat_at=now
                    )
                )
            else:
                record.heartbeat_at = now

    async def reclaim_expired(self) -> int:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            result = await session.execute(
                update(Delivery)
                .where(
                    Delivery.status == DeliveryStatus.PROCESSING.value,
                    Delivery.claim_expires_at < now,
                )
                .values(
                    status=DeliveryStatus.PENDING.value,
                    claimed_by=None,
                    claim_expires_at=None,
                    next_attempt_at=now,
                    updated_at=now,
                )
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def claim_one(self) -> str | None:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            candidate = await session.scalar(
                select(Delivery.id)
                .where(
                    Delivery.status.in_(
                        [DeliveryStatus.PENDING.value, DeliveryStatus.RETRY_WAIT.value]
                    ),
                    Delivery.next_attempt_at <= now,
                )
                .order_by(Delivery.next_attempt_at, Delivery.created_at)
                .limit(1)
            )
            if candidate is None:
                return None
            result = await session.execute(
                update(Delivery)
                .where(
                    Delivery.id == candidate,
                    Delivery.status.in_(
                        [DeliveryStatus.PENDING.value, DeliveryStatus.RETRY_WAIT.value]
                    ),
                    Delivery.next_attempt_at <= now,
                )
                .values(
                    status=DeliveryStatus.PROCESSING.value,
                    claimed_by=self.worker_id,
                    claim_expires_at=now + timedelta(seconds=self._lease_seconds),
                    updated_at=now,
                )
            )
            return candidate if result.rowcount == 1 else None  # type: ignore[attr-defined]

    async def process_one(self) -> bool:
        delivery_id = await self.claim_one()
        if delivery_id is None:
            return False
        message = await self._load_message(delivery_id)
        if message is None:
            await self._finish(
                delivery_id,
                ChannelResult(
                    False, False, "RECIPIENT_INVALID", "Recipient identity is unavailable"
                ),
                started_at=self._clock.now(),
            )
            return True
        if message.message_type == "voice" and not message.media_asset_id:
            try:
                if self._prepare_tts is None:
                    raise RuntimeError("TTS is not configured")
                asset_id = await self._prepare_tts(message.content or message.title)
                await self._set_media_asset(delivery_id, asset_id)
                message = replace(message, media_asset_id=asset_id)
            except Exception:
                await self._enable_text_fallback(delivery_id)
                message = replace(message, message_type="text", media_asset_id=None)
        attempt_started_at = self._clock.now()
        try:
            result = await self._channel.send(
                message
            )  # Network call intentionally outside DB transaction.
        except Exception as exc:
            result = ChannelResult(
                False,
                True,
                "CHANNEL_EXCEPTION",
                f"Channel adapter failed: {type(exc).__name__}",
            )
        if message.message_type == "voice" and result.error_code == "MEDIA_NOT_SENT":
            await self._enable_text_fallback(delivery_id)
            try:
                result = await self._channel.send(
                    replace(message, message_type="text", media_asset_id=None)
                )
            except Exception as exc:
                result = ChannelResult(
                    False,
                    True,
                    "CHANNEL_EXCEPTION",
                    f"Text fallback failed: {type(exc).__name__}",
                )
        await self._finish(
            delivery_id,
            result,
            sent_user_ids=message.recipients,
            started_at=attempt_started_at,
        )
        return True

    async def _set_media_asset(self, delivery_id: str, asset_id: str) -> None:
        async with self._factory() as session, session.begin():
            delivery = await session.scalar(
                select(Delivery)
                .where(Delivery.id == delivery_id)
                .options(selectinload(Delivery.notification))
            )
            if delivery is not None:
                delivery.notification.media_asset_id = asset_id

    async def _enable_text_fallback(self, delivery_id: str) -> None:
        async with self._factory() as session, session.begin():
            delivery = await session.scalar(
                select(Delivery)
                .where(Delivery.id == delivery_id)
                .options(selectinload(Delivery.notification))
            )
            if delivery is not None:
                payload = dict(delivery.notification.payload or {})
                payload["voice_text_fallback"] = True
                delivery.notification.payload = payload

    async def _load_message(self, delivery_id: str) -> ChannelMessage | None:
        async with self._factory() as session:
            delivery = await session.scalar(
                select(Delivery)
                .where(Delivery.id == delivery_id)
                .options(selectinload(Delivery.notification))
            )
            if delivery is None or delivery.status != DeliveryStatus.PROCESSING.value:
                return None
            notification = delivery.notification
            if (
                notification.reminder_id
                and notification.reminder_occurrence_id is None
                and delivery.recipient_id
            ):
                reminder = await session.get(Reminder, notification.reminder_id)
                if reminder is None or reminder.status != "active":
                    delivery.status = DeliveryStatus.CANCELLED.value
                    delivery.claimed_by = None
                    delivery.claim_expires_at = None
                    delivery.last_error_code = "REMINDER_INACTIVE"
                    delivery.last_error_message = "Reminder is no longer active"
                    delivery.updated_at = self._clock.now()
                    await session.commit()
                    return None
                recipient = await session.scalar(
                    select(ReminderRecipient).where(
                        ReminderRecipient.reminder_id == notification.reminder_id,
                        ReminderRecipient.person_id == delivery.recipient_id,
                    )
                )
                if recipient is not None and recipient.status != "pending":
                    delivery.status = DeliveryStatus.CANCELLED.value
                    delivery.claimed_by = None
                    delivery.claim_expires_at = None
                    delivery.last_error_code = "REMINDER_ACKNOWLEDGED"
                    delivery.last_error_message = "Reminder recipient already acknowledged"
                    delivery.updated_at = self._clock.now()
                    await session.commit()
                    return None
            expires_at = notification.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at and expires_at <= self._clock.now():
                delivery.status = DeliveryStatus.CANCELLED.value
                delivery.claimed_by = None
                delivery.claim_expires_at = None
                delivery.last_error_code = "NOTIFICATION_EXPIRED"
                delivery.last_error_message = "Notification expired before delivery"
                delivery.updated_at = self._clock.now()
                await session.commit()
                return None
            broadcast = delivery.recipient_type == RecipientType.BROADCAST.value
            if broadcast:
                recipients: list[str] = []
            else:
                identity = await session.scalar(
                    select(WeComIdentity).where(
                        WeComIdentity.person_id == delivery.recipient_id,
                        WeComIdentity.active.is_(True),
                    )
                )
                if identity is None:
                    return None
                recipients = [identity.user_id]
            payload = dict(notification.payload or {})
            action_ids = payload.pop("action_ids", {})
            if delivery.recipient_id and isinstance(action_ids, dict):
                action_id = action_ids.get(delivery.recipient_id)
                if isinstance(action_id, str) and self._action_token_for_id is not None:
                    payload["task_id"] = action_id
                    payload["action_token"] = self._action_token_for_id(action_id)
            return ChannelMessage(
                message_type=(
                    "text"
                    if notification.payload.get("voice_text_fallback")
                    else notification.message_type
                ),
                title=notification.title,
                content=notification.content,
                recipients=recipients,
                url=notification.url,
                image_url=notification.image_url,
                broadcast=broadcast,
                payload=payload,
                media_asset_id=notification.media_asset_id,
            )

    async def _finish(
        self,
        delivery_id: str,
        result: ChannelResult,
        *,
        sent_user_ids: list[str] | None = None,
        started_at: datetime | None = None,
    ) -> None:
        now = self._clock.now()
        attempt_started_at = started_at or now
        async with self._factory() as session, session.begin():
            delivery = await session.get(Delivery, delivery_id)
            if delivery is None or delivery.status != DeliveryStatus.PROCESSING.value:
                return
            delivery.attempt_count += 1
            if result.success:
                delivery.status, delivery.sent_at = DeliveryStatus.SUCCEEDED.value, now
                notification = await session.get(Notification, delivery.notification_id)
                if (
                    notification is not None
                    and notification.require_ack
                    and notification.reminder_occurrence_id is not None
                    and notification.payload.get("interactive_reminder") is True
                ):
                    if notification.payload.get("broadcast_reminder") is True:
                        audience = select(ReminderOccurrenceRecipient.person_id).where(
                            ReminderOccurrenceRecipient.occurrence_id
                            == notification.reminder_occurrence_id
                        )
                        identity_filter = WeComIdentity.person_id.in_(audience)
                    elif sent_user_ids:
                        identity_filter = WeComIdentity.user_id.in_(sent_user_ids)
                    else:
                        identity_filter = None
                    if identity_filter is not None:
                        await session.execute(
                            update(WeComIdentity)
                            .where(identity_filter, WeComIdentity.active.is_(True))
                            .values(
                                latest_interactive_occurrence_id=(
                                    notification.reminder_occurrence_id
                                ),
                                updated_at=now,
                            )
                        )
            elif result.retryable and delivery.attempt_count < delivery.max_attempts:
                delivery.status = DeliveryStatus.RETRY_WAIT.value
                index = min(delivery.attempt_count - 1, len(BACKOFF_SECONDS) - 1)
                delivery.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[index])
            else:
                delivery.status = DeliveryStatus.DEAD.value
            delivery.claimed_by = delivery.claim_expires_at = None
            delivery.last_error_code = result.error_code
            delivery.last_error_message = result.error_message
            delivery.provider_message_id = result.provider_message_id
            delivery.updated_at = now
            session.add(
                DeliveryAttempt(
                    id=new_id("attempt"),
                    delivery_id=delivery.id,
                    attempt_no=delivery.attempt_count,
                    status=(
                        AttemptStatus.SUCCEEDED.value
                        if result.success
                        else AttemptStatus.RETRYABLE_FAILURE.value
                        if result.retryable
                        else AttemptStatus.PERMANENT_FAILURE.value
                    ),
                    started_at=attempt_started_at,
                    finished_at=now,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    provider_status=result.provider_status,
                    provider_response=result.response_metadata or None,
                )
            )
