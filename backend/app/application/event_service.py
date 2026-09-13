from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.api.errors import AppError
from app.api.schemas import EventCreate
from app.application.platform_settings import read_xiaohongshu_publishing_enabled
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
from app.profiles.constants import DEFAULT_PROFILE_ID
from app.profiles.registry import ProfileRegistry
from app.profiles.routing import ProfileRoutingService
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class AcceptResult:
    event_id: str
    duplicate: bool


class EventService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        profiles: ProfileRegistry | None = None,
        routing: ProfileRoutingService | None = None,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._profiles = profiles
        self._routing = routing

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
        profile_id = client.profile_id or DEFAULT_PROFILE_ID
        if self._profiles is not None:
            profile_id = await self._profiles.resolve_id(profile_id)
        if self._routing is not None:
            await self._routing.resolve(profile_id, capability="outbound_enabled")
            if draft.broadcast:
                await self._routing.resolve(profile_id, capability="broadcast_enabled")
            else:
                await self._routing.validate_recipients(profile_id, draft.recipients)
        key = (profile_id, EventSource.API_CLIENT.value, client.id, draft.event_key)
        async with self._factory() as session:
            existing = await session.scalar(
                select(Event).where(
                    Event.profile_id == key[0],
                    Event.source_type == key[1],
                    Event.source_id == key[2],
                    Event.event_key == key[3],
                )
            )
            if existing is not None:
                return AcceptResult(existing.id, True)
            now = self._clock.now()
            event = Event(
                id=new_id("evt"),
                profile_id=profile_id,
                source_type=key[1],
                source_id=key[2],
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
                profile_id=profile_id,
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
                        profile_id=profile_id,
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
                        Event.profile_id == key[0],
                        Event.source_type == key[1],
                        Event.source_id == key[2],
                        Event.event_key == key[3],
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
        broadcast: bool = False,
        message_type: str = "text",
        level: str = "info",
        occurred_at: datetime | None = None,
        url: str | None = None,
        image_url: str | None = None,
        require_ack: bool = False,
        ack_policy: str | None = None,
        reminder_id: str | None = None,
        reminder_occurrence_id: str | None = None,
        media_asset_id: str | None = None,
        payload: dict[str, Any] | None = None,
        publish_to_mp: bool = False,
        publish_variants: list[dict[str, Any]] | None = None,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> AcceptResult:
        """Accept a trusted platform event through the same durable queue boundary."""
        if self._profiles is not None:
            profile_id = await self._profiles.resolve_id(profile_id)
        if self._routing is not None:
            await self._routing.resolve(profile_id, capability="outbound_enabled")
            if broadcast:
                await self._routing.resolve(profile_id, capability="broadcast_enabled")
            elif recipients and recipients != ["@all"]:
                await self._routing.validate_recipients(profile_id, recipients)
        effective_publish_variants = list(publish_variants or [])
        suppressed_publish_platforms: list[str] = []
        if any(
            isinstance(variant, dict) and variant.get("platform") == "xiaohongshu"
            for variant in effective_publish_variants
        ) and not await read_xiaohongshu_publishing_enabled(self._factory):
            effective_publish_variants = [
                variant
                for variant in effective_publish_variants
                if not (isinstance(variant, dict) and variant.get("platform") == "xiaohongshu")
            ]
            suppressed_publish_platforms.append("xiaohongshu")

        has_mp = publish_to_mp
        has_xhs = False
        if effective_publish_variants:
            for variant in effective_publish_variants:
                plat = (
                    variant.get("platform")
                    if isinstance(variant, dict)
                    else getattr(variant, "platform", None)
                )
                if plat == "wechat_mp":
                    has_mp = True
                elif plat == "xiaohongshu":
                    has_xhs = True

        has_publish = has_mp or has_xhs
        if has_publish:
            if broadcast:
                raise AppError(
                    "invalid_publish",
                    "Article publishing cannot be combined with broadcast",
                    422,
                )
            if message_type != "article":
                raise AppError(
                    "invalid_publish",
                    "Article publishing requires an article message",
                    422,
                )
        else:
            if broadcast and recipients != ["@all"]:
                raise AppError(
                    "invalid_broadcast", "Broadcast must use the sole recipient @all", 422
                )
            if not recipients and not suppressed_publish_platforms:
                raise AppError(
                    "recipient_required", "At least one explicit recipient is required", 422
                )
        key = (profile_id, source_type, source_id, event_key)
        async with self._factory() as session:
            existing = await session.scalar(
                select(Event).where(
                    Event.profile_id == key[0],
                    Event.source_type == key[1],
                    Event.source_id == key[2],
                    Event.event_key == key[3],
                )
            )
            if existing is not None:
                return AcceptResult(existing.id, True)
            now = self._clock.now()
            event = Event(
                id=new_id("evt"),
                profile_id=profile_id,
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
            merged_payload = dict(payload or {})
            if has_mp:
                merged_payload["publish_to_mp"] = True
            if effective_publish_variants:
                merged_payload["publish_variants"] = effective_publish_variants
            if suppressed_publish_platforms:
                merged_payload["publish_suppressed_platforms"] = suppressed_publish_platforms
            notification = Notification(
                id=new_id("ntf"),
                event=event,
                profile_id=profile_id,
                reminder_id=reminder_id,
                reminder_occurrence_id=reminder_occurrence_id,
                message_type=message_type,
                title=title,
                content=content,
                url=url,
                image_url=image_url,
                media_asset_id=media_asset_id,
                priority="critical" if level == "critical" else "normal",
                require_ack=require_ack,
                ack_policy=ack_policy,
                payload=merged_payload,
                created_at=now,
                expires_at=None,
            )
            session.add_all([event, notification])
            if has_mp:
                session.add(
                    Delivery(
                        id=new_id("dlv"),
                        notification=notification,
                        profile_id=profile_id,
                        channel="mp_article",
                        recipient_type=RecipientType.PUBLISH.value,
                        recipient_id=None,
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
            if has_xhs:
                session.add(
                    Delivery(
                        id=new_id("dlv"),
                        notification=notification,
                        profile_id=profile_id,
                        channel="xhs_article",
                        recipient_type=RecipientType.PUBLISH.value,
                        recipient_id=None,
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
            if broadcast:
                delivery_recipients: list[str | None] = [None]
            else:
                delivery_recipients = list(dict.fromkeys(recipients))
            for recipient_id in delivery_recipients:
                session.add(
                    Delivery(
                        id=new_id("dlv"),
                        notification=notification,
                        profile_id=profile_id,
                        channel="wecom",
                        recipient_type=(
                            RecipientType.BROADCAST.value
                            if broadcast
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
            try:
                await session.commit()
                return AcceptResult(event.id, False)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(Event).where(
                        Event.profile_id == key[0],
                        Event.source_type == key[1],
                        Event.source_id == key[2],
                        Event.event_key == key[3],
                    )
                )
                if existing is None:
                    raise
                return AcceptResult(existing.id, True)
