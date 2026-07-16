from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, cast

from app.application.conversation_service import ConversationService
from app.application.reminder_service import ReminderPermissionDenied, ReminderService
from app.application.wecom_menu_service import WeComMenuService
from app.channels.base import ChannelResult
from app.domain.clock import Clock, SystemClock
from app.infrastructure.database.reminder_models import IncomingMessage, InteractionEvent
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class InteractionWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reminders: ReminderService,
        conversations: ConversationService,
        emit_reply: Callable[[str, str, str], Awaitable[None]] | None = None,
        update_card: Callable[[str, str], Awaitable[ChannelResult]] | None = None,
        menu_service: WeComMenuService | None = None,
        clock: Clock | None = None,
        worker_id: str = "interaction-main",
    ) -> None:
        self._sessions = session_factory
        self._reminders = reminders
        self._conversations = conversations
        self._worker_id = worker_id
        self._emit_reply = emit_reply
        self._update_card = update_card
        self._menu_service = menu_service
        self._clock = clock or SystemClock()

    async def run_once(self, *, now: datetime | None = None, limit: int = 20) -> int:
        instant = now or self._clock.now()
        interaction_ids = await self._claim_interactions(instant, limit)
        processed = 0
        for event_id in interaction_ids:
            await self._process_interaction(event_id, instant)
            processed += 1
        message_ids = await self._claim_messages(limit)
        for message_id in message_ids:
            await self._process_message(message_id, instant)
            processed += 1
        return processed

    async def recover_stale_messages(self) -> int:
        """A single-instance restart makes every persisted processing claim stale."""
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(IncomingMessage)
                    .where(IncomingMessage.processing_status == "processing")
                    .values(processing_status="pending")
                ),
            )
            return int(result.rowcount or 0)

    async def _claim_interactions(self, now: datetime, limit: int) -> list[str]:
        ids: list[str] = []
        async with self._sessions() as session, session.begin():
            candidates = await session.scalars(
                select(InteractionEvent.id)
                .where(
                    InteractionEvent.status.in_(("pending", "processing")),
                    InteractionEvent.attempt_count < InteractionEvent.max_attempts,
                    (
                        InteractionEvent.claim_expires_at.is_(None)
                        | (InteractionEvent.claim_expires_at < now)
                    ),
                )
                .order_by(InteractionEvent.received_at)
                .limit(limit)
            )
            for event_id in candidates:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(InteractionEvent)
                        .where(
                            InteractionEvent.id == event_id,
                            InteractionEvent.status.in_(("pending", "processing")),
                            InteractionEvent.attempt_count < InteractionEvent.max_attempts,
                            (
                                InteractionEvent.claim_expires_at.is_(None)
                                | (InteractionEvent.claim_expires_at < now)
                            ),
                        )
                        .values(
                            status="processing",
                            claimed_by=self._worker_id,
                            claim_expires_at=now + timedelta(minutes=2),
                        )
                    ),
                )
                if result.rowcount == 1:
                    ids.append(event_id)
        return ids

    async def _process_interaction(self, event_id: str, now: datetime) -> None:
        async with self._sessions() as session:
            event = await session.get(InteractionEvent, event_id)
            if event is None or event.claimed_by != self._worker_id:
                return
            action_id = event.notification_action_id
            sender = event.sender_external_id
            response_code = event.response_code
        try:
            if action_id is None:
                raise ReminderPermissionDenied("unknown or expired action token")
            result = await self._reminders.acknowledge_action_id(
                action_id=action_id, sender_wecom_userid=sender, now=now
            )
            status = "processed"
            outcome = result.result
            if response_code and self._update_card is not None:
                try:
                    card_result = await self._update_card(response_code, sender)
                    if not card_result.success:
                        logger.warning(
                            "wecom_card_update_failed",
                            extra={
                                "interaction_event_id": event_id,
                                "error_code": card_result.error_code,
                            },
                        )
                except Exception:
                    logger.warning(
                        "wecom_card_update_failed",
                        extra={"interaction_event_id": event_id},
                        exc_info=True,
                    )
        except ReminderPermissionDenied:
            status = "rejected"
            outcome = "forbidden"
        except Exception as exc:
            async with self._sessions() as session, session.begin():
                event = await session.get(InteractionEvent, event_id)
                if event and event.claimed_by == self._worker_id:
                    event.attempt_count += 1
                    event.status = (
                        "dead" if event.attempt_count >= event.max_attempts else "pending"
                    )
                    event.last_error = type(exc).__name__
                    event.claimed_by = None
                    event.claim_expires_at = None
            return
        async with self._sessions() as session, session.begin():
            event = await session.get(InteractionEvent, event_id)
            if event and event.claimed_by == self._worker_id:
                event.status = status
                event.result = outcome
                event.response_code = None  # response_code is short-lived and never retained.
                event.last_error = None
                event.processed_at = now
                event.claimed_by = None
                event.claim_expires_at = None

    async def _claim_messages(self, limit: int) -> list[str]:
        async with self._sessions() as session, session.begin():
            candidates = list(
                await session.scalars(
                    select(IncomingMessage.id)
                    .where(
                        IncomingMessage.processing_status == "pending",
                        IncomingMessage.message_type.in_(("text", "voice", "event")),
                    )
                    .order_by(IncomingMessage.received_at)
                    .limit(limit)
                )
            )
            ids: list[str] = []
            for message_id in candidates:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(IncomingMessage)
                        .where(
                            IncomingMessage.id == message_id,
                            IncomingMessage.processing_status == "pending",
                        )
                        .values(processing_status="processing")
                    ),
                )
                if result.rowcount == 1:
                    ids.append(message_id)
            return ids

    async def _process_message(self, message_id: str, now: datetime) -> None:
        async with self._sessions() as session:
            message = await session.get(IncomingMessage, message_id)
            if message is None:
                return
            sender, text = message.sender_external_id, message.text
            message_type = message.message_type
            event_key = message.event_payload.get("event_key")
            event_name = message.event_payload.get("event")
            has_action = message.event_payload.get("has_action") is True
            event_payload = dict(message.event_payload)
            event_payload_changed = False
        status, error = "processed", None
        if (
            message_type == "event"
            and not has_action
            and event_name == "click"
            and isinstance(event_key, str)
            and self._menu_service
        ):
            try:
                menu_reply = await self._menu_service.handle(
                    sender, event_key, incoming_message_id=message_id
                )
                if self._emit_reply is not None:
                    await self._emit_reply(sender, message_id, menu_reply.text)
            except Exception as exc:
                attempts = int(event_payload.get("menu_reply_attempts") or 0) + 1
                event_payload["menu_reply_attempts"] = attempts
                event_payload_changed = True
                status = "pending" if attempts < 5 else "failed"
                error = type(exc).__name__
        elif message_type == "event":
            # Card actions have their own InteractionEvent and non-click events
            # do not belong to the menu command layer.
            pass
        elif text:
            try:
                conversation_reply = await self._conversations.handle_text(
                    sender_wecom_userid=sender, text=text, now=now
                )
                if self._emit_reply is not None:
                    await self._emit_reply(sender, message_id, conversation_reply.text)
            except Exception as exc:
                status, error = "failed", type(exc).__name__
        elif error is None:
            status, error = "failed", "missing_recognition_text"
        async with self._sessions() as session, session.begin():
            message = await session.get(IncomingMessage, message_id)
            if message:
                message.processing_status = status
                message.error_message = error
                if event_payload_changed:
                    message.event_payload = {
                        **message.event_payload,
                        "menu_reply_attempts": event_payload["menu_reply_attempts"],
                    }
                message.processed_at = now if status != "pending" else None
