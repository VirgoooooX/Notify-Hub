from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime

import structlog
from app.application.reminder_service import ReminderService
from app.domain.clock import Clock, SystemClock


class ReminderWorker:
    def __init__(
        self,
        service: ReminderService,
        worker_id: str = "reminder-main",
        clock: Clock | None = None,
    ) -> None:
        self._service = service
        self._worker_id = worker_id
        self._clock = clock or SystemClock()
        self._log = structlog.get_logger(__name__)

    async def run_once(self, *, now: datetime | None = None) -> int:
        instant = now or self._clock.now()
        claimed = await self._service.claim_due(worker_id=self._worker_id, now=instant)
        processed = 0
        for reminder_id in claimed:
            try:
                result = await self._service.trigger_claimed(
                    reminder_id, worker_id=self._worker_id, now=instant
                )
                if result is not None:
                    processed += 1
            except Exception as exc:
                self._log.exception(
                    "reminder_trigger_failed",
                    reminder_id=reminder_id,
                    error_type=type(exc).__name__,
                )

        broadcasts = await self._service.claim_due_broadcasts(
            worker_id=self._worker_id, now=instant
        )
        for occurrence_id in broadcasts:
            try:
                result = await self._service.notify_broadcast(
                    occurrence_id, worker_id=self._worker_id, now=instant
                )
                if result is not None:
                    processed += 1
            except Exception as exc:
                self._log.exception(
                    "reminder_broadcast_failed",
                    occurrence_id=occurrence_id,
                    error_type=type(exc).__name__,
                )

        due_recipients = await self._service.claim_due_recipients(
            worker_id=self._worker_id, now=instant
        )
        for recipient_id in due_recipients:
            try:
                result = await self._service.notify_recipient(
                    recipient_id, worker_id=self._worker_id, now=instant
                )
                if result is not None:
                    processed += 1
            except Exception as exc:
                self._log.exception(
                    "reminder_recipient_notification_failed",
                    recipient_id=recipient_id,
                    error_type=type(exc).__name__,
                )
        return processed

    async def run(self, stop: asyncio.Event, poll_seconds: float = 5.0) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                self._log.exception(
                    "reminder_worker_iteration_failed", error_type=type(exc).__name__
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
