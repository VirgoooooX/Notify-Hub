from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from app.infrastructure.database.reminder_models import (
    Reminder,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
    ReminderRecipient,
)
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement


class MobileReminderNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class MobileReminderDetail:
    reminder: Reminder
    occurrences: list[dict[str, object]]


class MobileReminderQueryService:
    """Member-scoped Reminder reads for the WeCom mobile surface."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _authorized(person_id: str) -> ColumnElement[bool]:
        return or_(
            Reminder.creator_person_id == person_id,
            exists(
                select(ReminderRecipient.id).where(
                    ReminderRecipient.reminder_id == Reminder.id,
                    ReminderRecipient.person_id == person_id,
                )
            ),
        )

    async def list(
        self,
        person_id: str,
        *,
        scope: Literal["active", "awaiting_ack", "today", "all"],
        now: datetime,
        timezone: str,
    ) -> list[Reminder]:
        statement = select(Reminder).where(self._authorized(person_id))
        if scope == "active":
            statement = statement.where(Reminder.status.in_(("active", "paused")))
        elif scope == "awaiting_ack":
            statement = statement.where(
                Reminder.status == "active",
                exists(
                    select(ReminderOccurrenceRecipient.id)
                    .join(
                        ReminderOccurrence,
                        ReminderOccurrence.id == ReminderOccurrenceRecipient.occurrence_id,
                    )
                    .where(
                        ReminderOccurrence.reminder_id == Reminder.id,
                        ReminderOccurrenceRecipient.person_id == person_id,
                        ReminderOccurrenceRecipient.status == "pending",
                    )
                ),
            )
        elif scope == "today":
            zone = ZoneInfo(timezone)
            local_day = now.astimezone(zone).date()
            start = datetime.combine(local_day, time.min, zone).astimezone(UTC)
            end = datetime.combine(local_day, time.max, zone).astimezone(UTC)
            statement = statement.where(
                or_(
                    Reminder.next_run_at.between(start, end),
                    exists(
                        select(ReminderOccurrence.id).where(
                            ReminderOccurrence.reminder_id == Reminder.id,
                            ReminderOccurrence.scheduled_for.between(start, end),
                        )
                    ),
                )
            )
        async with self._sessions() as session:
            return list(
                await session.scalars(statement.order_by(Reminder.next_run_at, Reminder.created_at))
            )

    async def detail(self, reminder_id: str, person_id: str) -> MobileReminderDetail:
        async with self._sessions() as session:
            reminder = await session.scalar(
                select(Reminder).where(
                    Reminder.id == reminder_id,
                    self._authorized(person_id),
                )
            )
            if reminder is None:
                raise MobileReminderNotFound(reminder_id)
            occurrences = list(
                await session.scalars(
                    select(ReminderOccurrence)
                    .where(ReminderOccurrence.reminder_id == reminder_id)
                    .order_by(ReminderOccurrence.scheduled_for.desc())
                    .limit(20)
                )
            )
            occurrence_ids = [item.id for item in occurrences]
            recipient_by_occurrence: dict[str, ReminderOccurrenceRecipient] = {}
            if occurrence_ids:
                recipients = await session.scalars(
                    select(ReminderOccurrenceRecipient).where(
                        ReminderOccurrenceRecipient.occurrence_id.in_(occurrence_ids),
                        ReminderOccurrenceRecipient.person_id == person_id,
                    )
                )
                recipient_by_occurrence = {item.occurrence_id: item for item in recipients}
        return MobileReminderDetail(
            reminder,
            [
                {
                    "id": occurrence.id,
                    "scheduled_for": occurrence.scheduled_for,
                    "status": occurrence.status,
                    "completed_at": occurrence.completed_at,
                    "recipient": (
                        {
                            "status": recipient.status,
                            "notify_count": recipient.notify_count,
                            "next_notify_at": recipient.next_notify_at,
                            "acknowledged_at": recipient.acknowledged_at,
                        }
                        if (recipient := recipient_by_occurrence.get(occurrence.id))
                        else None
                    ),
                }
                for occurrence in occurrences
            ],
        )
