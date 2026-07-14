from __future__ import annotations

from app.application.event_service import EventService
from app.application.reminder_service import EventAcceptance, ReminderEventDraft
from app.infrastructure.database.models import WeComIdentity
from app.infrastructure.security.secret_store import SecretStore
from app.plugin_runtime.base import EventDraft, EventReceipt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class PluginEventEmitterAdapter:
    def __init__(self, events: EventService) -> None:
        self._events = events

    async def emit(self, plugin_id: str, event: EventDraft) -> EventReceipt:
        result = await self._events.accept_internal_event(
            source_type="plugin",
            source_id=plugin_id,
            event_type=event.event_type,
            event_key=event.event_key,
            title=event.title,
            content=event.content,
            recipients=event.recipients or [],
            message_type=event.message_type,
            level=event.level,
            occurred_at=event.occurred_at,
            url=str(event.url) if event.url else None,
            image_url=str(event.image_url) if event.image_url else None,
            require_ack=event.require_ack,
            payload=event.payload,
        )
        return EventReceipt(
            event_id=result.event_id,
            status="duplicate" if result.duplicate else "accepted",
        )


class ReminderEventEmitterAdapter:
    def __init__(self, events: EventService) -> None:
        self._events = events

    async def __call__(self, draft: ReminderEventDraft) -> EventAcceptance:
        result = await self._events.accept_internal_event(
            source_type=draft.source_type,
            source_id=draft.source_id,
            event_type=draft.event_type,
            event_key=draft.event_key,
            title=draft.title,
            content=draft.content,
            recipients=list(draft.recipients),
            message_type=draft.message_type,
            require_ack=draft.require_ack,
            ack_policy=draft.ack_policy,
            reminder_id=draft.source_id,
            payload=draft.payload,
        )
        return EventAcceptance(
            event_id=result.event_id,
            accepted=True,
            duplicate=result.duplicate,
        )


class PluginSecretResolverAdapter:
    def __init__(self, store: SecretStore | None) -> None:
        self._store = store

    async def resolve(self, plugin_id: str, name: str) -> str:
        if self._store is None:
            raise RuntimeError("plugin secret storage is not configured")
        value = await self._store.get("plugin", plugin_id, name)
        if value is None:
            raise KeyError(f"plugin secret {name!r} is not configured")
        return value


class ConversationReplyEmitterAdapter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        events: EventService,
    ) -> None:
        self._sessions = sessions
        self._events = events

    async def __call__(self, sender_userid: str, message_id: str, text: str) -> None:
        async with self._sessions() as session:
            person_id = await session.scalar(
                select(WeComIdentity.person_id).where(
                    WeComIdentity.user_id == sender_userid,
                    WeComIdentity.active.is_(True),
                )
            )
        if person_id is None:
            return
        await self._events.accept_internal_event(
            source_type="system",
            source_id="conversation",
            event_type="conversation.reply",
            event_key=f"conversation-reply-{message_id}",
            title="Notify Hub",
            content=text,
            recipients=[person_id],
        )
