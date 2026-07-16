from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from app.infrastructure.database.models import Delivery
from app.infrastructure.database.reminder_draft_models import ReminderDraft
from app.infrastructure.database.reminder_models import (
    IncomingMessage,
    InteractionEvent,
    Reminder,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class CleanupResult:
    interaction_events: int
    incoming_messages: int
    reminder_drafts: int
    dry_run: bool


class ReminderMaintenanceService:
    """Bounded operational controls for reminder queues and disposable history."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def metrics(self) -> dict[str, int]:
        async with self._sessions() as session:
            queries = {
                "active_reminders": select(func.count(Reminder.id)).where(
                    Reminder.status == "active"
                ),
                "active_occurrences": select(func.count(ReminderOccurrence.id)).where(
                    ReminderOccurrence.status == "active"
                ),
                "pending_occurrence_recipients": select(
                    func.count(ReminderOccurrenceRecipient.id)
                ).where(ReminderOccurrenceRecipient.status == "pending"),
                "pending_deliveries": select(func.count(Delivery.id)).where(
                    Delivery.status.in_(("pending", "retry_wait", "processing"))
                ),
                "dead_deliveries": select(func.count(Delivery.id)).where(Delivery.status == "dead"),
                "dead_interactions": select(func.count(InteractionEvent.id)).where(
                    InteractionEvent.status == "dead"
                ),
                "failed_messages": select(func.count(IncomingMessage.id)).where(
                    IncomingMessage.processing_status == "failed"
                ),
            }
            return {name: int(await session.scalar(query) or 0) for name, query in queries.items()}

    async def retry_interaction(self, event_id: str) -> bool:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(InteractionEvent)
                    .where(InteractionEvent.id == event_id, InteractionEvent.status == "dead")
                    .values(
                        status="pending",
                        attempt_count=0,
                        last_error=None,
                        claimed_by=None,
                        claim_expires_at=None,
                        processed_at=None,
                    )
                ),
            )
            return bool(result.rowcount)

    async def retry_message(self, message_id: str) -> bool:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(IncomingMessage)
                    .where(
                        IncomingMessage.id == message_id,
                        IncomingMessage.processing_status == "failed",
                    )
                    .values(processing_status="pending", error_message=None, processed_at=None)
                ),
            )
            return bool(result.rowcount)

    async def cleanup(
        self, *, before: datetime, dry_run: bool = True, limit: int = 1000
    ) -> CleanupResult:
        if limit < 1 or limit > 10_000:
            raise ValueError("cleanup limit must be between 1 and 10000")
        async with self._sessions() as session, session.begin():
            interaction_ids = list(
                await session.scalars(
                    select(InteractionEvent.id)
                    .where(
                        InteractionEvent.status.in_(("processed", "rejected", "dead")),
                        InteractionEvent.received_at < before,
                    )
                    .limit(limit)
                )
            )
            message_ids = list(
                await session.scalars(
                    select(IncomingMessage.id)
                    .where(
                        IncomingMessage.processing_status.in_(("processed", "failed")),
                        IncomingMessage.received_at < before,
                    )
                    .limit(limit)
                )
            )
            draft_ids = list(
                await session.scalars(
                    select(ReminderDraft.id)
                    .where(
                        ReminderDraft.status.in_(("confirmed", "cancelled", "expired")),
                        ReminderDraft.updated_at < before,
                    )
                    .limit(limit)
                )
            )
            if not dry_run:
                if interaction_ids:
                    await session.execute(
                        delete(InteractionEvent).where(InteractionEvent.id.in_(interaction_ids))
                    )
                if message_ids:
                    await session.execute(
                        delete(IncomingMessage).where(IncomingMessage.id.in_(message_ids))
                    )
                if draft_ids:
                    await session.execute(
                        delete(ReminderDraft).where(ReminderDraft.id.in_(draft_ids))
                    )
        return CleanupResult(
            interaction_events=len(interaction_ids),
            incoming_messages=len(message_ids),
            reminder_drafts=len(draft_ids),
            dry_run=dry_run,
        )
