from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog
from app.application.plugin_service import PluginService


class PluginWorker:
    """Single-instance database-backed scheduler and runner."""

    def __init__(
        self,
        service: PluginService,
        *,
        worker_id: str,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._service = service
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._logger = structlog.get_logger().bind(worker_id=worker_id, worker_type="plugin")

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        await self._service.initialize()
        self._task = asyncio.create_task(self._loop(), name=f"plugin-worker:{self._worker_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_once(self) -> bool:
        await self._service.enqueue_due()
        run_id = await self._service.claim_next(self._worker_id)
        if run_id is None:
            return False
        try:
            await self._service.execute(run_id)
        except Exception:
            self._logger.exception("plugin worker iteration failed", plugin_run_id=run_id)
        return True

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_once()
            except Exception as exc:
                self._logger.exception(
                    "plugin_scheduler_iteration_failed", error_type=type(exc).__name__
                )
                worked = False
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
