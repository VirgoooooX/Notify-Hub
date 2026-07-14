from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.api.errors import AppError
from app.api.schemas import EventCreate
from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import (
    ApiClient,
    Delivery,
    DeliveryStatus,
    Event,
    EventSource,
    EventStatus,
    Notification,
    RecipientType,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class AcceptResult:
    event_id: str
    duplicate: bool


class EventService:
    def __init__(self, factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._factory = factory
        self._clock = clock

    def _authorize(self, client: ApiClient, draft: EventCreate) -> None:
        if client.allowed_event_types and draft.event_type not in client.allowed_event_types:
            raise AppError("event_type_forbidden", "API client cannot submit this event type", 403)
        if draft.level == "critical" and not client.allow_critical:
            raise AppError("priority_forbidden", "API client cannot submit critical events", 403)
        if draft.message_type in {"image", "voice"} and not client.allow_media:
            raise AppError("media_forbidden", "API client cannot submit media events", 403)
        if draft.message_type == "voice" and not client.allow_voice:
            raise AppError("voice_forbidden", "API client cannot submit voice events", 403)
        if draft.message_type == "voice" and draft.level != "critical":
            raise AppError(
                "voice_priority_required", "Voice delivery requires critical priority", 403
            )
        if draft.broadcast:
            if not client.allow_broadcast:
                raise AppError("broadcast_forbidden", "API client cannot broadcast", 403)
        else:
            disallowed = set(draft.recipients) - set(client.allowed_recipient_ids)
            if disallowed:
                raise AppError(
                    "recipient_forbidden",
                    "API client cannot use one or more recipients",
                    403,
                    {"recipient_ids": sorted(disallowed)},
                )

    async def accept_api_event(self, client: ApiClient, draft: EventCreate) -> AcceptResult:
        self._authorize(client, draft)
        key = (EventSource.API_CLIENT.value, client.id, draft.event_key)
        async with self._factory() as session:
            existing = await session.scalar(
                select(Event).where(
                    Event.source_type == key[0],
                    Event.source_id == key[1],
                    Event.event_key == key[2],
                )
            )
            if existing is not None:
                return AcceptResult(existing.id, True)
            now = self._clock.now()
            event = Event(
                id=new_id("evt"),
                source_type=key[0],
                source_id=key[1],
                event_type=draft.event_type,
                event_key=draft.event_key,
                title=draft.title,
                content=draft.content,
                level=draft.level,
                url=str(draft.url) if draft.url else None,
                image_url=str(draft.image_url) if draft.image_url else None,
                payload=draft.payload,
                occurred_at=draft.occurred_at or now,
                accepted_at=now,
                status=EventStatus.ROUTED.value,
                ignore_reason=None,
            )
            notification = Notification(
                id=new_id("ntf"),
                event=event,
                message_type=draft.message_type,
                title=draft.title,
                content=draft.content,
                url=str(draft.url) if draft.url else None,
                image_url=str(draft.image_url) if draft.image_url else None,
                reminder_id=None,
                media_asset_id=draft.media_asset_id,
                ack_policy=None,
                payload=draft.payload,
                priority="critical" if draft.level == "critical" else "normal",
                require_ack=draft.require_ack,
                created_at=now,
                expires_at=None,
            )
            session.add_all([event, notification])
            recipients = [None] if draft.broadcast else draft.recipients
            for recipient_id in recipients:
                session.add(
                    Delivery(
                        id=new_id("dlv"),
                        notification=notification,
                        channel="wecom",
                        recipient_type=(
                            RecipientType.BROADCAST.value
                            if draft.broadcast
                            else RecipientType.PERSON.value
                        ),
                        recipient_id=recipient_id,
                        status=DeliveryStatus.PENDING.value,
                        attempt_count=0,
                        max_attempts=5,
                        next_attempt_at=now,
                        claimed_by=None,
                        claim_expires_at=None,
                        last_error_code=None,
                        last_error_message=None,
                        provider_message_id=None,
                        sent_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.execute(
                update(ApiClient).where(ApiClient.id == client.id).values(last_used_at=now)
            )
            try:
                await session.commit()
                return AcceptResult(event.id, False)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(Event).where(
                        Event.source_type == key[0],
                        Event.source_id == key[1],
                        Event.event_key == key[2],
                    )
                )
                if existing is None:
                    raise
                return AcceptResult(existing.id, True)

    async def accept_internal_event(
        self,
        *,
        source_type: str,
        source_id: str,
        event_type: str,
        event_key: str,
        title: str,
        content: str,
        recipients: list[str],
        message_type: str = "text",
        level: str = "info",
        occurred_at: datetime | None = None,
        url: str | None = None,
        image_url: str | None = None,
        require_ack: bool = False,
        ack_policy: str | None = None,
        reminder_id: str | None = None,
        media_asset_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AcceptResult:
        """Accept a trusted platform event through the same durable queue boundary."""
        if not recipients:
            raise AppError("recipient_required", "At least one explicit recipient is required", 422)
        key = (source_type, source_id, event_key)
        async with self._factory() as session:
            existing = await session.scalar(
                select(Event).where(
                    Event.source_type == key[0],
                    Event.source_id == key[1],
                    Event.event_key == key[2],
                )
            )
            if existing is not None:
                return AcceptResult(existing.id, True)
            now = self._clock.now()
            event = Event(
                id=new_id("evt"),
                source_type=source_type,
                source_id=source_id,
                event_type=event_type,
                event_key=event_key,
                title=title,
                content=content,
                level=level,
                url=url,
                image_url=image_url,
                payload=payload or {},
                occurred_at=occurred_at or now,
                accepted_at=now,
                status=EventStatus.ROUTED.value,
                ignore_reason=None,
            )
            notification = Notification(
                id=new_id("ntf"),
                event=event,
                reminder_id=reminder_id,
                message_type=message_type,
                title=title,
                content=content,
                url=url,
                image_url=image_url,
                media_asset_id=media_asset_id,
                priority="critical" if level == "critical" else "normal",
                require_ack=require_ack,
                ack_policy=ack_policy,
                payload=payload or {},
                created_at=now,
                expires_at=None,
            )
            session.add_all([event, notification])
            for recipient_id in dict.fromkeys(recipients):
                session.add(
                    Delivery(
                        id=new_id("dlv"),
                        notification=notification,
                        channel="wecom",
                        recipient_type=RecipientType.PERSON.value,
                        recipient_id=recipient_id,
                        status=DeliveryStatus.PENDING.value,
                        attempt_count=0,
                        max_attempts=5,
                        next_attempt_at=now,
                        claimed_by=None,
                        claim_expires_at=None,
                        last_error_code=None,
                        last_error_message=None,
                        provider_message_id=None,
                        sent_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            try:
                await session.commit()
                return AcceptResult(event.id, False)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(Event).where(
                        Event.source_type == key[0],
                        Event.source_id == key[1],
                        Event.event_key == key[2],
                    )
                )
                if existing is None:
                    raise
                return AcceptResult(existing.id, True)
