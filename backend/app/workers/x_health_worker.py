from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog
from app.application.x_health_service import XHealthService


class XHealthWorker:
    """Independent source watchdog; it does not depend on plugin circuit state."""

    def __init__(self, service: XHealthService, *, poll_seconds: float) -> None:
        self._service = service
        self._poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._logger = structlog.get_logger().bind(worker_type="x_source_health")

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="x-source-health-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_once(self) -> int:
        try:
            return await self._service.run_once()
        except Exception:
            self._logger.exception("x_source_health_iteration_failed")
            return 0

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
