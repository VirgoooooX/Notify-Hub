from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr

HOST_IDS = Literal["istoreos", "zspace-nas", "oc-chicago", "cloudcone-la", "zgo-la"]
SECRET_NAMES = {
    "istoreos": "istoreos_agent",
    "zspace-nas": "zspace_nas_agent",
    "oc-chicago": "oc_chicago_agent",
    "cloudcone-la": "cloudcone_la_agent",
    "zgo-la": "zgo_la_agent",
}


class HostTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_id: HOST_IDS
    enabled: bool = True
    include_containers: list[str] = Field(default_factory=list, max_length=200)
    exclude_containers: list[str] = Field(default_factory=list, max_length=200)

    @property
    def secret_name(self) -> str:
        return SECRET_NAMES[self.host_id]


def default_hosts() -> list[HostTarget]:
    return [HostTarget(host_id=host_id) for host_id in SECRET_NAMES]


class DockerLogMonitorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    recipients: list[str] = Field(default_factory=list, max_length=100)
    hosts: list[HostTarget] = Field(default_factory=default_hosts, max_length=5)
    first_run_mode: Literal["baseline", "scan_recent"] = "baseline"
    lookback_seconds: int = Field(default=600, ge=60, le=3600)
    log_tail: int = Field(default=2000, ge=100, le=10000)
    repeat_cooldown_seconds: int = Field(default=1800, ge=300, le=86400)
    default_error_threshold: int = Field(default=3, ge=1, le=100)
    default_warning_threshold: int = Field(default=5, ge=1, le=100)


class AgentConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: AnyHttpUrl
    token: SecretStr


class AlertState(BaseModel):
    first_seen_at: datetime
    last_seen_at: datetime
    occurrences: int = Field(ge=0)
    notified_at: datetime | None = None


class ContainerState(BaseModel):
    container_id: str = ""
    running: bool = False
    status: str = "unknown"
    health: str | None = None
    restart_count: int = 0
    oom_killed: bool = False
    last_seen_at: datetime | None = None


class AgentHealthState(BaseModel):
    consecutive_failures: int = Field(default=0, ge=0)
    incident_started_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
    notified: bool = False


class MonitorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    initialized: bool = False
    last_scan_at: datetime | None = None
    containers: dict[str, ContainerState] = Field(default_factory=dict)
    alerts: dict[str, AlertState] = Field(default_factory=dict)
    agent_health: dict[str, AgentHealthState] = Field(default_factory=dict)


class EventDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    event_key: str
    title: str
    content: str = ""
    level: Literal["info", "warning", "critical"] = "warning"
    occurred_at: datetime | None = None
    url: AnyHttpUrl | None = None
    image_url: AnyHttpUrl | None = None
    recipients: list[str] | None = None
    require_ack: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class HttpResponse(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class HttpClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,  # noqa: ASYNC109 - mirrors the runtime HTTP client contract.
    ) -> HttpResponse: ...


class PluginContext(Protocol):
    http: HttpClient
    logger: Any

    async def get_config(self) -> Mapping[str, Any]: ...

    async def get_state(self, key: str, default: Any = None) -> Any: ...

    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int: ...

    async def get_secret(self, name: str) -> str: ...

    async def emit_event(self, event: EventDraft) -> Any: ...


class PluginRunResult(BaseModel):
    status: Literal["disabled", "baseline_initialized", "success"]
    emitted_events: int = 0
    message: str | None = None
