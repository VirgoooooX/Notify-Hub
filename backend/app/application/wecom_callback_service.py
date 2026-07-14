from __future__ import annotations

from dataclasses import dataclass

from app.channels.wecom.callback import IncomingCallback
from app.domain.reminders import hash_action_token
from app.infrastructure.database.base import new_id
from app.infrastructure.database.reminder_models import (
    IncomingMessage,
    InteractionEvent,
    NotificationAction,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class CallbackReceipt:
    incoming_message_id: str
    duplicate: bool
    interaction_event_id: str | None = None


class WeComCallbackService:
    """Persists verified callback facts before any conversation side effects."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def accept(self, callback: IncomingCallback) -> CallbackReceipt:
        incoming = IncomingMessage(
            id=new_id("inm"),
            channel="wecom",
            sender_external_id=callback.sender_external_id,
            provider_message_id=callback.provider_message_id,
            dedupe_key=callback.dedupe_key,
            message_type=callback.message_type,
            text=callback.text,
            media_refs=callback.media_refs,
            event_payload={
                "event": callback.event,
                "has_action": callback.action_token is not None,
            },
            received_at=callback.received_at,
            processed_at=None,
            processing_status="pending",
            error_message=None,
        )
        interaction: InteractionEvent | None = None
        try:
            async with self._sessions() as session, session.begin():
                session.add(incoming)
                if callback.action_token:
                    token_hash = hash_action_token(callback.action_token)
                    action_id = await session.scalar(
                        select(NotificationAction.id).where(
                            NotificationAction.token_hash == token_hash
                        )
                    )
                    interaction = InteractionEvent(
                        id=new_id("int"),
                        channel="wecom",
                        dedupe_key=callback.dedupe_key,
                        sender_external_id=callback.sender_external_id,
                        notification_action_id=action_id,
                        action="complete",
                        status="pending",
                        result=None,
                        response_code=callback.response_code,
                        claimed_by=None,
                        claim_expires_at=None,
                        received_at=callback.received_at,
                        processed_at=None,
                    )
                    session.add(interaction)
                await session.flush()
        except IntegrityError:
            async with self._sessions() as session:
                existing = await session.scalar(
                    select(IncomingMessage).where(
                        IncomingMessage.channel == "wecom",
                        IncomingMessage.dedupe_key == callback.dedupe_key,
                    )
                )
                existing_interaction = await session.scalar(
                    select(InteractionEvent).where(
                        InteractionEvent.channel == "wecom",
                        InteractionEvent.dedupe_key == callback.dedupe_key,
                    )
                )
                if existing is None:
                    raise
                return CallbackReceipt(
                    existing.id,
                    True,
                    existing_interaction.id if existing_interaction else None,
                )
        return CallbackReceipt(incoming.id, False, interaction.id if interaction else None)
