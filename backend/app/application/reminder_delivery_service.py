from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Any, cast

from app.infrastructure.database.models import Delivery, Event, Notification
from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ReminderDeliveryService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], object],
    ) -> None:
        self._sessions = session_factory
        self._clock = clock

    async def cancel_pending(
        self,
        reminder_id: str,
        *,
        occurrence_id: str | None = None,
        recipient_id: str | None = None,
    ) -> int:
        async with self._sessions() as session, session.begin():
            if occurrence_id is not None:
                notification_ids = select(Notification.id).where(
                    Notification.reminder_occurrence_id == occurrence_id
                )
            else:
                reminder_events: Select[tuple[str]] = select(Event.id).where(
                    Event.source_type == "reminder", Event.source_id == reminder_id
                )
                notification_ids = select(Notification.id).where(
                    Notification.event_id.in_(reminder_events)
                )
            delivery_filter: builtins.list[Any] = [
                Delivery.notification_id.in_(notification_ids),
                Delivery.status.in_(("pending", "retry_wait")),
            ]
            if recipient_id is not None:
                delivery_filter.append(Delivery.recipient_id == recipient_id)
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(Delivery)
                    .where(*delivery_filter)
                    .values(
                        status="cancelled",
                        last_error_code="reminder_acknowledged",
                        last_error_message="reminder stopped before delivery",
                        updated_at=self._clock(),
                    )
                ),
            )
            return int(result.rowcount or 0)
