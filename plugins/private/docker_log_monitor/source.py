from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from .schemas import AgentConnection, HostTarget, PluginContext

HTTP_TIMEOUT = 15.0
STATE_KEY = "monitor_state"


class AgentSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerSnapshot:
    name: str
    container_id: str
    running: bool
    status: str
    health: str | None
    restart_count: int
    oom_killed: bool
    logs: str = ""


@dataclass
class HostScan:
    host_id: str
    containers: list[ContainerSnapshot] = field(default_factory=list)
    error: str | None = None
    partial_errors: list[str] = field(default_factory=list)


def _endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _json(response: Any, description: str) -> Any:
    if response.status_code < 200 or response.status_code >= 300:
        raise AgentSourceError(f"{description} returned HTTP {response.status_code}")
    try:
        return response.json()
    except (ValueError, TypeError) as exc:
        raise AgentSourceError(f"{description} returned invalid JSON") from exc


async def _load_connection(context: PluginContext, target: HostTarget) -> AgentConnection:
    try:
        raw = await context.get_secret(target.secret_name)
        return AgentConnection.model_validate(json.loads(raw))
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise AgentSourceError("agent connection secret is missing or invalid") from exc


async def scan_host(
    context: PluginContext,
    target: HostTarget,
    *,
    since: int | None,
    log_tail: int,
) -> HostScan:
    try:
        connection = await _load_connection(context, target)
        base_url = str(connection.base_url).rstrip("/")
        headers = {"Authorization": f"Bearer {connection.token.get_secret_value()}"}
        response = await context.http.get(
            _endpoint(base_url, "/api/agent/docker/containers/json"),
            params={"all": "1"},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        rows = _json(response, "container list")
        if not isinstance(rows, list):
            raise AgentSourceError("container list returned an invalid shape")

        containers: list[ContainerSnapshot] = []
        partial_errors: list[str] = []
        for row in rows[:200]:
            if not isinstance(row, dict):
                continue
            names = row.get("Names")
            name = str(names[0] if isinstance(names, list) and names else row.get("Id", "unknown"))
            name = name.lstrip("/")
            if target.include_containers and name not in target.include_containers:
                continue
            if name in target.exclude_containers:
                continue
            container_id = str(row.get("Id", ""))
            if not container_id:
                continue

            try:
                inspect_response = await context.http.get(
                    _endpoint(base_url, f"/api/agent/docker/containers/{container_id}/json"),
                    headers=headers,
                    timeout=HTTP_TIMEOUT,
                )
                details = _json(inspect_response, "container inspection")
                state = details.get("State", {}) if isinstance(details, dict) else {}
                health_info = state.get("Health") if isinstance(state, dict) else None
                health = health_info.get("Status") if isinstance(health_info, dict) else None
                log_params = {
                    "stdout": "1",
                    "stderr": "1",
                    "timestamps": "1",
                    "tail": str(log_tail),
                }
                if since is not None:
                    log_params["since"] = str(max(0, since))
                log_response = await context.http.get(
                    _endpoint(base_url, f"/api/agent/docker/containers/{container_id}/logs"),
                    params=log_params,
                    headers=headers,
                    timeout=HTTP_TIMEOUT,
                )
                if log_response.status_code < 200 or log_response.status_code >= 300:
                    raise AgentSourceError(
                        f"container logs returned HTTP {log_response.status_code}"
                    )
                containers.append(
                    ContainerSnapshot(
                        name=name,
                        container_id=container_id,
                        running=state.get("Running") is True,
                        status=str(state.get("Status", row.get("State", "unknown"))),
                        health=str(health) if health is not None else None,
                        restart_count=int(details.get("RestartCount", 0) or 0),
                        oom_killed=state.get("OOMKilled") is True,
                        logs=log_response.text[:2_000_000],
                    )
                )
            except AgentSourceError as exc:
                partial_errors.append(f"{name}: {exc}")
            except Exception as exc:
                partial_errors.append(f"{name}: request failed: {type(exc).__name__}")
        return HostScan(
            host_id=target.host_id,
            containers=containers,
            partial_errors=partial_errors[:200],
        )
    except AgentSourceError as exc:
        return HostScan(host_id=target.host_id, error=str(exc))
    except Exception as exc:
        return HostScan(host_id=target.host_id, error=f"agent request failed: {type(exc).__name__}")


def scan_since(last_scan: datetime | None, lookback_seconds: int) -> int | None:
    if last_scan is None:
        return None
    if last_scan.tzinfo is None:
        last_scan = last_scan.replace(tzinfo=UTC)
    return int(last_scan.timestamp()) - min(60, lookback_seconds // 2)
