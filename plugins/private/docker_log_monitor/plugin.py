from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .rules import RuleDecision, classify_line, normalize_log_line
from .schemas import (
    AgentHealthState,
    AlertState,
    ContainerState,
    DockerLogMonitorConfig,
    EventDraft,
    MonitorState,
    PluginContext,
    PluginRunResult,
)
from .source import HostScan, scan_host, scan_since

STATE_KEY = "monitor_state"


def _receipt_status(receipt: Any) -> str | None:
    value = receipt.get("status") if isinstance(receipt, dict) else getattr(receipt, "status", None)
    return str(value).lower() if value is not None else None


def _safe_excerpt(line: str, max_length: int = 500) -> str:
    text = normalize_log_line(line)
    text = re.sub(
        r"(?i)\b(authorization|cookie|token|secret|password|passwd|api[_-]?key)\b\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(r"https?://([^/\s:]+)(?::\d+)?[^\s]*", r"https://\1/<path>", text)
    text = re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", "<ip>", text)
    text = re.sub(r"\b[0-9a-fA-F]{12,}\b", "<id>", text)
    return text[:max_length]


def _fingerprint(line: str) -> str:
    text = _safe_excerpt(line, 240).casefold()
    text = re.sub(r"\b\d+\b", "<n>", text)
    return re.sub(r"\s+", " ", text).strip()


def _alert_key(host_id: str, container: str, rule_id: str, fingerprint: str) -> str:
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"{host_id}/{container}/{rule_id}/{digest}"


def _event_key(alert_key: str, state: AlertState) -> str:
    episode = int(state.first_seen_at.timestamp())
    return f"docker-log-{hashlib.sha256(alert_key.encode()).hexdigest()[:16]}-{episode}"


class DockerLogMonitorPlugin:
    plugin_id = "docker_log_monitor"
    api_version = "1"
    version = "0.1.4"

    @classmethod
    def metadata(cls) -> dict[str, str]:
        return {"id": cls.plugin_id, "name": "Docker Log Monitor", "version": cls.version}

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return DockerLogMonitorConfig.model_json_schema()

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        return DockerLogMonitorConfig.model_validate(config).model_dump(mode="json")

    async def run(self, context: PluginContext) -> PluginRunResult:
        config = DockerLogMonitorConfig.model_validate(await context.get_config())
        if not config.enabled:
            return PluginRunResult(status="disabled")

        state = MonitorState.model_validate(await context.get_state(STATE_KEY, None) or {})
        now = datetime.now(UTC)
        since = scan_since(state.last_scan_at, config.lookback_seconds)
        if not state.initialized and config.first_run_mode == "baseline":
            since = None

        targets = [target for target in config.hosts if target.enabled]
        scans = await self._scan_targets(context, targets, since, config.log_tail)
        if not state.initialized and config.first_run_mode == "baseline":
            self._baseline(state, scans, now)
            await context.set_state(STATE_KEY, state.model_dump(mode="json"))
            return PluginRunResult(
                status="baseline_initialized", message="current container state recorded"
            )

        emitted = 0
        for scan in scans:
            emitted += await self._process_scan(context, config, state, scan, now)

        state.initialized = True
        state.last_scan_at = now
        self._prune_state(state, now)
        await context.set_state(STATE_KEY, state.model_dump(mode="json"))
        context.logger.info(
            "docker_log_monitor_scan_complete",
            hosts=len(scans),
            emitted_events=emitted,
        )
        return PluginRunResult(status="success", emitted_events=emitted)

    @staticmethod
    def _prune_state(state: MonitorState, now: datetime) -> None:
        alert_cutoff = now - timedelta(days=7)
        state.alerts = dict(
            sorted(
                (
                    (key, value)
                    for key, value in state.alerts.items()
                    if value.last_seen_at >= alert_cutoff
                ),
                key=lambda item: item[1].last_seen_at,
                reverse=True,
            )[:1000]
        )
        container_cutoff = now - timedelta(days=30)
        state.containers = dict(
            sorted(
                (
                    (key, value)
                    for key, value in state.containers.items()
                    if value.last_seen_at is not None and value.last_seen_at >= container_cutoff
                ),
                key=lambda item: item[1].last_seen_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )[:500]
        )

    async def _scan_targets(
        self,
        context: PluginContext,
        targets: list[Any],
        since: int | None,
        log_tail: int,
    ) -> list[HostScan]:
        import asyncio

        return list(
            await asyncio.gather(
                *(scan_host(context, target, since=since, log_tail=log_tail) for target in targets)
            )
        )

    @staticmethod
    def _baseline(state: MonitorState, scans: list[HostScan], now: datetime) -> None:
        for scan in scans:
            if scan.error:
                health = state.agent_health.setdefault(scan.host_id, AgentHealthState())
                health.consecutive_failures += 1
                health.incident_started_at = health.incident_started_at or now
                health.last_failure_at = now
                continue
            state.agent_health[scan.host_id] = AgentHealthState(last_success_at=now)
            for container in scan.containers:
                state.containers[f"{scan.host_id}/{container.name}"] = ContainerState(
                    container_id=container.container_id,
                    running=container.running,
                    status=container.status,
                    health=container.health,
                    restart_count=container.restart_count,
                    oom_killed=container.oom_killed,
                    last_seen_at=now,
                )
        state.initialized = True
        state.last_scan_at = now

    async def _process_scan(
        self,
        context: PluginContext,
        config: DockerLogMonitorConfig,
        state: MonitorState,
        scan: HostScan,
        now: datetime,
    ) -> int:
        if scan.error:
            return await self._record_agent_failure(context, config, state, scan, now)

        state.agent_health[scan.host_id] = AgentHealthState(last_success_at=now)
        if scan.partial_errors:
            context.logger.warning(
                "docker_log_monitor_partial_scan",
                host_id=scan.host_id,
                skipped_containers=len(scan.partial_errors),
            )

        emitted = 0
        for container in scan.containers:
            key = f"{scan.host_id}/{container.name}"
            previous = state.containers.get(key)
            if previous is not None:
                status_decision = self._status_decision(container, previous)
                if status_decision is not None:
                    emitted += await self._emit_alert(
                        context,
                        config,
                        state,
                        alert_key=f"{key}/{status_decision[0]}",
                        rule_id=status_decision[0],
                        container=container.name,
                        level=status_decision[1],
                        threshold=status_decision[3],
                        excerpt=status_decision[2],
                        occurrences=status_decision[3],
                        now=now,
                    )

            grouped: dict[tuple[str, str], tuple[RuleDecision, str, int]] = {}
            for raw_line in container.logs.splitlines():
                decision = classify_line(
                    container.name,
                    raw_line,
                    config.default_error_threshold,
                    config.default_warning_threshold,
                )
                if decision is None:
                    continue
                fingerprint = decision.fingerprint or _fingerprint(raw_line)
                group_key = (decision.rule_id, fingerprint)
                current = grouped.get(group_key)
                grouped[group_key] = (
                    decision,
                    raw_line,
                    (current[2] if current is not None else 0) + 1,
                )
            ranked = sorted(
                grouped.items(),
                key=lambda item: (item[1][0].level == "critical", item[1][2]),
                reverse=True,
            )
            if ranked:
                (rule_id, fingerprint), (decision, excerpt, count) = ranked[0]
                emitted += await self._emit_alert(
                    context,
                    config,
                    state,
                    alert_key=_alert_key(scan.host_id, container.name, rule_id, fingerprint),
                    rule_id=rule_id,
                    container=container.name,
                    level=decision.level,
                    threshold=decision.threshold,
                    excerpt=_safe_excerpt(excerpt),
                    occurrences=count,
                    now=now,
                )
            state.containers[key] = ContainerState(
                container_id=container.container_id,
                running=container.running,
                status=container.status,
                health=container.health,
                restart_count=container.restart_count,
                oom_killed=container.oom_killed,
                last_seen_at=now,
            )
        return emitted

    async def _record_agent_failure(
        self,
        context: PluginContext,
        config: DockerLogMonitorConfig,
        state: MonitorState,
        scan: HostScan,
        now: datetime,
    ) -> int:
        health = state.agent_health.setdefault(scan.host_id, AgentHealthState())
        health.consecutive_failures += 1
        health.incident_started_at = health.incident_started_at or now
        health.last_failure_at = now
        if health.consecutive_failures < 3 or health.notified:
            return 0

        alert_key = f"{scan.host_id}/agent_unavailable"
        alert = AlertState(
            first_seen_at=health.incident_started_at,
            last_seen_at=now,
            occurrences=health.consecutive_failures,
        )
        excerpt = f"无法连续读取 {scan.host_id} 的 Fleetge Agent：{scan.error}"
        receipt = await context.emit_event(
            EventDraft(
                event_type="docker.container_log_error",
                event_key=_event_key(alert_key, alert),
                title=f"Docker 异常｜{scan.host_id} / Fleetge Agent",
                content=(
                    f"主机：{scan.host_id}\n容器：Fleetge Agent\n规则：agent_unavailable\n"
                    f"连续失败：{health.consecutive_failures}\n\n{excerpt}"
                ),
                level="warning",
                occurred_at=now,
                recipients=config.recipients or None,
                payload={
                    "host_id": scan.host_id,
                    "container": "Fleetge Agent",
                    "rule_id": "agent_unavailable",
                    "consecutive_failures": health.consecutive_failures,
                    "threshold": 3,
                    "excerpt": excerpt,
                },
            )
        )
        status = _receipt_status(receipt)
        if status not in {"accepted", "duplicate"}:
            raise RuntimeError(f"event was not accepted: {status or 'missing status'}")
        health.notified = True
        return 1 if status == "accepted" else 0

    @staticmethod
    def _status_decision(
        container: Any, previous: ContainerState
    ) -> tuple[str, Literal["info", "warning", "critical"], str, int] | None:
        if container.oom_killed and not previous.oom_killed:
            return "container_oom_killed", "critical", "容器被 OOMKilled", 1
        if container.health == "unhealthy" and previous.health != "unhealthy":
            return "container_unhealthy", "critical", "容器健康检查变为 unhealthy", 1
        if not container.running and previous.running:
            return "container_stopped", "critical", f"容器状态变为 {container.status}", 1
        restart_delta = container.restart_count - previous.restart_count
        if restart_delta > 0:
            return (
                "container_restart_loop",
                "critical",
                f"容器在监控窗口内累计重启 {restart_delta} 次",
                3,
            )
        return None

    async def _emit_alert(
        self,
        context: PluginContext,
        config: DockerLogMonitorConfig,
        state: MonitorState,
        *,
        alert_key: str,
        rule_id: str,
        container: str,
        level: Literal["info", "warning", "critical"],
        threshold: int,
        excerpt: str,
        occurrences: int,
        now: datetime,
    ) -> int:
        alert = state.alerts.get(alert_key)
        cooldown = timedelta(seconds=config.repeat_cooldown_seconds)
        if alert is None or now - alert.last_seen_at > cooldown:
            alert = AlertState(first_seen_at=now, last_seen_at=now, occurrences=0)
        alert.last_seen_at = now
        alert.occurrences += occurrences
        state.alerts[alert_key] = alert
        if alert.occurrences < threshold:
            return 0
        if alert.notified_at is not None and now - alert.notified_at <= cooldown:
            return 0

        host_id = alert_key.split("/", maxsplit=1)[0]
        title = f"Docker 异常｜{host_id} / {container}"
        content = (
            f"主机：{host_id}\n容器：{container}\n规则：{rule_id}\n"
            f"累计命中：{alert.occurrences}\n\n{excerpt}"
        )
        receipt = await context.emit_event(
            EventDraft(
                event_type="docker.container_log_error",
                event_key=_event_key(alert_key, alert),
                title=title,
                content=content[:9000],
                level=level,
                occurred_at=now,
                recipients=config.recipients or None,
                payload={
                    "host_id": host_id,
                    "container": container,
                    "rule_id": rule_id,
                    "occurrences": alert.occurrences,
                    "threshold": threshold,
                    "excerpt": excerpt,
                },
            )
        )
        status = _receipt_status(receipt)
        if status not in {"accepted", "duplicate"}:
            raise RuntimeError(f"event was not accepted: {status or 'missing status'}")
        alert.notified_at = now
        return 1 if status == "accepted" else 0
