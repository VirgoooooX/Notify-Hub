from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.application.conversation_service import ConversationService
from app.application.reminder_service import ReminderPermissionDenied, ReminderService
from app.infrastructure.database.reminder_models import IncomingMessage, InteractionEvent
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class InteractionWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reminders: ReminderService,
        conversations: ConversationService,
        emit_reply: Callable[[str, str, str], Awaitable[None]] | None = None,
        transcribe_voice: Callable[[str], Awaitable[str]] | None = None,
        worker_id: str = "interaction-main",
    ) -> None:
        self._sessions = session_factory
        self._reminders = reminders
        self._conversations = conversations
        self._worker_id = worker_id
        self._emit_reply = emit_reply
        self._transcribe_voice = transcribe_voice

    async def run_once(self, *, now: datetime | None = None, limit: int = 20) -> int:
        instant = now or datetime.now(UTC)
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
        try:
            if action_id is None:
                raise ReminderPermissionDenied("unknown or expired action token")
            result = await self._reminders.acknowledge_action_id(
                action_id=action_id, sender_wecom_userid=sender, now=now
            )
            status = "processed"
            outcome = result.result
        except ReminderPermissionDenied:
            status = "rejected"
            outcome = "forbidden"
        async with self._sessions() as session, session.begin():
            event = await session.get(InteractionEvent, event_id)
            if event and event.claimed_by == self._worker_id:
                event.status = status
                event.result = outcome
                event.response_code = None  # response_code is short-lived and never retained.
                event.processed_at = now
                event.claimed_by = None
                event.claim_expires_at = None

    async def _claim_messages(self, limit: int) -> list[str]:
        async with self._sessions() as session, session.begin():
            ids = list(
                await session.scalars(
                    select(IncomingMessage.id)
                    .where(
                        IncomingMessage.processing_status == "pending",
                        IncomingMessage.message_type.in_(("text", "voice")),
                    )
                    .order_by(IncomingMessage.received_at)
                    .limit(limit)
                )
            )
            if ids:
                await session.execute(
                    update(IncomingMessage)
                    .where(IncomingMessage.id.in_(ids))
                    .values(processing_status="processing")
                )
            return ids

    async def _process_message(self, message_id: str, now: datetime) -> None:
        async with self._sessions() as session:
            message = await session.get(IncomingMessage, message_id)
            if message is None:
                return
            sender, text = message.sender_external_id, message.text
            message_type = message.message_type
            media_id = message.media_refs.get("MediaId")
        status, error = "processed", None
        if (
            not text
            and message_type == "voice"
            and isinstance(media_id, str)
            and self._transcribe_voice is not None
        ):
            try:
                text = await self._transcribe_voice(media_id)
            except Exception as exc:
                status, error = "failed", type(exc).__name__
        if text:
            try:
                reply = await self._conversations.handle_text(
                    sender_wecom_userid=sender, text=text, now=now
                )
                if self._emit_reply is not None:
                    await self._emit_reply(sender, message_id, reply.text)
            except Exception as exc:
                status, error = "failed", type(exc).__name__
        elif error is None:
            status, error = "failed", "missing_recognition_text"
        async with self._sessions() as session, session.begin():
            message = await session.get(IncomingMessage, message_id)
            if message:
                message.processing_status = status
                message.error_message = error
                message.processed_at = now
