from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.plugin_runtime.registry import PluginRegistry

from plugins.private.docker_log_monitor.plugin import DockerLogMonitorPlugin
from plugins.private.docker_log_monitor.schemas import (
    AgentHealthState,
    ContainerState,
    DockerLogMonitorConfig,
    MonitorState,
)
from plugins.private.docker_log_monitor.source import ContainerSnapshot, HostScan


class FakeContext:
    def __init__(self, receipts: list[str] | None = None) -> None:
        self.events: list[Any] = []
        self.receipts = list(receipts or ["accepted"])
        self.logger = SimpleNamespace(warning=lambda *_args, **_kwargs: None)

    async def emit_event(self, event: Any) -> Any:
        self.events.append(event)
        return SimpleNamespace(status=self.receipts.pop(0))


@pytest.mark.asyncio
async def test_repeated_log_fingerprint_emits_once_with_redacted_excerpt() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext()
    state = MonitorState(initialized=True)
    config = DockerLogMonitorConfig(hosts=[], recipients=["person_admin"])
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    line = (
        "ERROR token=super-secret-value no space left on device request "
        "https://api.example.com/private?id=123 from 192.168.31.50"
    )
    scan = HostScan(
        host_id="istoreos",
        containers=[
            ContainerSnapshot(
                name="notify-hub",
                container_id="abc",
                running=True,
                status="running",
                health="healthy",
                restart_count=0,
                oom_killed=False,
                logs=line,
            )
        ],
    )
    state.containers["istoreos/notify-hub"] = ContainerState(running=True)

    assert await plugin._process_scan(context, config, state, scan, now) == 1
    assert await plugin._process_scan(context, config, state, scan, now) == 0
    assert len(context.events) == 1
    rendered = repr(context.events[0])
    assert "super-secret-value" not in rendered
    assert "192.168.31.50" not in rendered
    assert "/private?id=123" not in rendered


@pytest.mark.asyncio
async def test_notify_hub_plugin_failures_group_across_dynamic_run_ids() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext()
    state = MonitorState(initialized=True)
    state.containers["istoreos/notify-hub"] = ContainerState(running=True)
    first = (
        '2026-08-09T00:30:13Z {"plugin_id":"codex_x_monitor",'
        '"plugin_run_id":"prun_first","error_type":"AIGatewayError",'
        '"event":"plugin run failed","level":"error",'
        '"timestamp":"2026-08-09T00:30:13Z"}'
    )
    second = (
        '2026-08-09T00:32:08Z {"plugin_id":"codex_x_monitor",'
        '"plugin_run_id":"prun_second","error_type":"AIGatewayError",'
        '"event":"plugin run failed","level":"error",'
        '"timestamp":"2026-08-09T00:32:08Z"}'
    )
    scan = HostScan(
        host_id="istoreos",
        containers=[
            ContainerSnapshot(
                name="notify-hub",
                container_id="abc",
                running=True,
                status="running",
                health="healthy",
                restart_count=0,
                oom_killed=False,
                logs=f"{first}\n{second}",
            )
        ],
    )

    emitted = await plugin._process_scan(
        context,
        DockerLogMonitorConfig(hosts=[]),
        state,
        scan,
        datetime(2026, 8, 9, 0, 33, tzinfo=UTC),
    )

    assert emitted == 1
    assert len(context.events) == 1
    assert context.events[0].payload["rule_id"] == "notify_hub_plugin_run_failed"
    assert context.events[0].payload["occurrences"] == 2
    assert len(state.alerts) == 1


@pytest.mark.asyncio
async def test_restart_delta_emits_lifecycle_event() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext()
    state = MonitorState(initialized=True)
    state.containers["zspace-nas/MoviePilot_v2"] = ContainerState(running=True, restart_count=1)
    scan = HostScan(
        host_id="zspace-nas",
        containers=[
            ContainerSnapshot(
                name="MoviePilot_v2",
                container_id="abc",
                running=True,
                status="running",
                health="healthy",
                restart_count=4,
                oom_killed=False,
            )
        ],
    )
    emitted = await plugin._process_scan(
        context,
        DockerLogMonitorConfig(hosts=[]),
        state,
        scan,
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )
    assert emitted == 1
    assert context.events[0].payload["rule_id"] == "container_restart_loop"


@pytest.mark.asyncio
async def test_agent_failure_requires_three_consecutive_failed_scans() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext()
    state = MonitorState(initialized=True)
    for _ in range(3):
        emitted = await plugin._process_scan(
            context,
            DockerLogMonitorConfig(hosts=[]),
            state,
            HostScan(host_id="cloudcone-la", error="container list returned HTTP 401"),
            datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        )
    assert emitted == 1
    assert context.events[0].level == "warning"
    assert context.events[0].payload["host_id"] == "cloudcone-la"
    assert context.events[0].payload["consecutive_failures"] == 3


@pytest.mark.asyncio
async def test_agent_success_resets_consecutive_failures() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext()
    state = MonitorState(initialized=True)
    config = DockerLogMonitorConfig(hosts=[])
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    failure = HostScan(host_id="oc-chicago", error="agent request failed: ReadTimeout")
    success = HostScan(host_id="oc-chicago")
    for scan in [failure, failure, success, failure, failure]:
        assert await plugin._process_scan(context, config, state, scan, now) == 0

    assert context.events == []
    assert state.agent_health["oc-chicago"].consecutive_failures == 2


@pytest.mark.asyncio
async def test_agent_incident_notifies_only_once_until_recovery() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext(receipts=["accepted", "accepted"])
    state = MonitorState(initialized=True)
    config = DockerLogMonitorConfig(hosts=[])
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    failure = HostScan(host_id="oc-chicago", error="agent request failed: ReadTimeout")

    for _ in range(5):
        await plugin._process_scan(context, config, state, failure, now)
    assert len(context.events) == 1

    await plugin._process_scan(context, config, state, HostScan(host_id="oc-chicago"), now)
    for _ in range(3):
        await plugin._process_scan(context, config, state, failure, now)
    assert len(context.events) == 2


@pytest.mark.asyncio
async def test_partial_container_error_does_not_count_as_agent_failure() -> None:
    plugin = DockerLogMonitorPlugin()
    context = FakeContext()
    state = MonitorState(initialized=True)
    state.agent_health["oc-chicago"] = AgentHealthState(consecutive_failures=2)

    emitted = await plugin._process_scan(
        context,
        DockerLogMonitorConfig(hosts=[]),
        state,
        HostScan(host_id="oc-chicago", partial_errors=["app: request failed: ReadTimeout"]),
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )

    assert emitted == 0
    assert state.agent_health["oc-chicago"].consecutive_failures == 0
    assert context.events == []


def test_actual_private_plugin_is_discoverable() -> None:
    root = Path(__file__).parents[2]
    registry = PluginRegistry({"private": root})
    discovered = registry.discover()
    assert [item.manifest.id for item in discovered] == ["docker_log_monitor"]
    assert registry.errors == {}
