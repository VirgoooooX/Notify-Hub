from dataclasses import dataclass

from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Delivery, Event, Notification
from app.profiles.constants import DEFAULT_PROFILE_ID
from app.profiles.registry import ProfileRegistry
from app.profiles.routing import ProfileRoutingService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class NotificationDraft:
    title: str
    content: str
    message_type: str
    recipients: list[str]
    priority: str = "normal"
    url: str | None = None
    image_url: str | None = None
    media_asset_id: str | None = None
    require_ack: bool = False
    event_type: str = "system.direct_notification"
    profile_id: str = DEFAULT_PROFILE_ID


class NotificationService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        *,
        profiles: ProfileRegistry | None = None,
        routing: ProfileRoutingService | None = None,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._profiles = profiles
        self._routing = routing

    async def create(self, draft: NotificationDraft) -> str:
        profile_id = draft.profile_id
        if self._profiles is not None:
            profile_id = await self._profiles.resolve_id(profile_id)
        if self._routing is not None:
            await self._routing.resolve(profile_id, capability="outbound_enabled")
            await self._routing.validate_recipients(profile_id, draft.recipients)
        now = self._clock.now()
        event = Event(
            id=new_id("evt"),
            profile_id=profile_id,
            source_type="system",
            source_id="admin",
            event_type=draft.event_type,
            event_key=new_id("system"),
            title=draft.title,
            content=draft.content,
            level="critical" if draft.priority == "critical" else "info",
            url=draft.url,
            image_url=draft.image_url,
            payload={},
            occurred_at=now,
            accepted_at=now,
            status="routed",
            ignore_reason=None,
        )
        notification = Notification(
            id=new_id("ntf"),
            event=event,
            profile_id=profile_id,
            message_type=draft.message_type,
            title=draft.title,
            content=draft.content,
            url=draft.url,
            image_url=draft.image_url,
            reminder_id=None,
            media_asset_id=draft.media_asset_id,
            ack_policy=None,
            payload={},
            priority=draft.priority,
            require_ack=draft.require_ack,
            created_at=now,
            expires_at=None,
        )
        async with self._factory() as session, session.begin():
            session.add_all([event, notification])
            for recipient in draft.recipients:
                session.add(
                    Delivery(
                        id=new_id("dlv"),
                        notification=notification,
                        profile_id=profile_id,
                        channel="wecom",
                        recipient_type="person",
                        recipient_id=recipient,
                        status="pending",
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
        return notification.id
