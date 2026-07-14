from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.plugin_runtime.base import PluginRunResult
from app.plugin_runtime.context import PluginContext


@dataclass(frozen=True)
class RunOutcome:
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]
    result: PluginRunResult | None = None
    error_type: str | None = None
    error_message: str | None = None


class RunnablePlugin(Protocol):
    async def run(self, context: Any) -> PluginRunResult: ...


class PluginRunner:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[RunOutcome]] = {}

    async def run(
        self,
        *,
        run_id: str,
        plugin_id: str,
        plugin: RunnablePlugin,
        context: PluginContext,
        timeout_seconds: float,
    ) -> RunOutcome:
        lock = self._locks.setdefault(plugin_id, asyncio.Lock())
        if lock.locked():
            return RunOutcome(
                "failed", error_type="ConcurrencyLimit", error_message="already running"
            )

        async def execute() -> RunOutcome:
            async with lock:
                try:
                    async with asyncio.timeout(timeout_seconds):
                        start = getattr(plugin, "start", None)
                        if start is not None:
                            await start(context)
                        result = await plugin.run(context)
                        return RunOutcome("succeeded", result=result)
                except TimeoutError:
                    return RunOutcome(
                        "timed_out", error_type="PluginTimeout", error_message="run timed out"
                    )
                except asyncio.CancelledError:
                    return RunOutcome(
                        "cancelled", error_type="PluginCancelled", error_message="run cancelled"
                    )
                except Exception as exc:
                    context.logger.exception("plugin run failed", error_type=type(exc).__name__)
                    return RunOutcome(
                        "failed", error_type=type(exc).__name__, error_message=str(exc)[:500]
                    )
                finally:
                    try:
                        stop = getattr(plugin, "stop", None)
                        if stop is not None:
                            await asyncio.wait_for(stop(), timeout=5)
                    except Exception:
                        context.logger.exception("plugin stop failed")

        task = asyncio.create_task(execute(), name=f"plugin:{plugin_id}:{run_id}")
        self._tasks[run_id] = task
        try:
            return await task
        finally:
            self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True
