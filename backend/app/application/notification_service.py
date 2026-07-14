from dataclasses import dataclass

from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Delivery, Event, Notification
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


class NotificationService:
    def __init__(self, factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._factory = factory
        self._clock = clock

    async def create(self, draft: NotificationDraft) -> str:
        now = self._clock.now()
        event = Event(
            id=new_id("evt"),
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
