from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
OUT_OF_MEMORY_RE = re.compile(r"(?i)\b(oomkilled|out of memory|cannot allocate memory)\b")
SEGFAULT_RE = re.compile(r"(?i)\b(segmentation fault|segfault|core dumped)\b")
STORAGE_FULL_RE = re.compile(r"(?i)\b(no space left on device|disk quota exceeded)\b")
READ_ONLY_RE = re.compile(r"(?i)\b(read-only file system|filesystem is read-only)\b")
CORRUPTION_RE = re.compile(
    r"(?i)\b(database disk image is malformed|database corruption|corrupt(?:ed|ion) database|"
    r"checksum mismatch)\b"
)
SUPERVISOR_RE = re.compile(r"(?i)\b(supervisor not listening|failed to start supervisor)\b")


@dataclass(frozen=True)
class RuleDecision:
    rule_id: str
    level: Literal["info", "warning", "critical"]
    threshold: int
    fingerprint: str | None = None


@dataclass(frozen=True)
class ContainerProfile:
    error_threshold: int
    warning_threshold: int


# Explicit profiles for every container observed on the four reachable Docker hosts on 2026-07-28.
# Quiet services still have their own thresholds so a future error does not inherit an accidental
# global behavior. Unknown/new containers deliberately use the administrator-configured defaults.
CONTAINER_PROFILES: dict[str, ContainerProfile] = {
    "notify-hub": ContainerProfile(1, 2),
    "cli-proxy-api": ContainerProfile(3, 5),
    "rsshub-rsshub-1": ContainerProfile(3, 5),
    "rsshub-redis-1": ContainerProfile(2, 5),
    "cloud-media-sync": ContainerProfile(3, 5),
    "fleetge": ContainerProfile(2, 3),
    "fleetge-agent": ContainerProfile(1, 2),
    "fb3": ContainerProfile(2, 4),
    "mdc": ContainerProfile(2, 3),
    "emby_cms": ContainerProfile(2, 3),
    "rsshub-browserless-1": ContainerProfile(2, 5),
    "vaultwarden": ContainerProfile(2, 4),
    "superng6_qbittorrent": ContainerProfile(3, 5),
    "family-finance": ContainerProfile(2, 3),
    "moviepilot_115": ContainerProfile(2, 4),
    "moviepilot_v2": ContainerProfile(2, 4),
    "readflow-postgres": ContainerProfile(2, 4),
    "readflow-server": ContainerProfile(5, 8),
    "linuxserver_transmission": ContainerProfile(3, 5),
    "minio": ContainerProfile(1, 2),
    "lucky": ContainerProfile(3, 5),
    "homepage": ContainerProfile(2, 4),
    "alist": ContainerProfile(3, 5),
    "qbittorrent": ContainerProfile(3, 5),
    "ota-analyzer": ContainerProfile(2, 4),
    "asset-manage-asset-manage-1": ContainerProfile(2, 3),
    "dailyreport-dashboard-1": ContainerProfile(1, 2),
    "obsidian-sync-db": ContainerProfile(1, 2),
    "issue-analyzer-issue-analyzer-1": ContainerProfile(2, 3),
    "wxchat": ContainerProfile(1, 2),
    "it-tools": ContainerProfile(1, 2),
}


def _structured_log(line: str) -> dict[str, Any] | None:
    payload_start = line.find("{")
    if payload_start < 0:
        return None
    try:
        payload = json.loads(line[payload_start:])
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _stable_field(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().casefold())
    return text[:100] or fallback


def _notify_hub_plugin_failure(
    container: str,
    line: str,
    default_error: int,
) -> RuleDecision | None:
    if container != "notify-hub":
        return None
    payload = _structured_log(line)
    if payload is None:
        return None
    if str(payload.get("event", "")).casefold() != "plugin run failed":
        return None
    if str(payload.get("level", "")).casefold() not in {"error", "critical"}:
        return None

    plugin_id = _stable_field(payload.get("plugin_id"), "unknown_plugin")
    error_type = _stable_field(payload.get("error_type"), "unknown_error")
    profile = CONTAINER_PROFILES.get(container)
    threshold = profile.error_threshold if profile is not None else default_error
    return RuleDecision(
        "notify_hub_plugin_run_failed",
        "warning",
        threshold,
        fingerprint=f"{plugin_id}:{error_type}",
    )


def normalize_log_line(line: str) -> str:
    return re.sub(r"\s+", " ", ANSI_RE.sub("", line)).strip()


def _ignored(container: str, line: str) -> bool:
    container = container.casefold()
    lowered = line.casefold()
    if container == "rsshub-browserless-1" and "current period usage" in lowered:
        return True
    if container == "cloud-media-sync" and '"state": true' in lowered:
        return True
    if container == "readflow-postgres" and "connection to client lost" in lowered:
        return True
    if container == "vaultwarden" and (
        "2fa token not provided" in lowered or "blocked address" in lowered
    ):
        return True
    if container in {"qbittorrent", "superng6_qbittorrent", "ota-analyzer", "lucky"} and any(
        phrase in lowered
        for phrase in (
            "requestparser::parseresult",
            "invalid http request",
            "tls handshake error",
        )
    ):
        return True
    if container == "fb3" and any(
        phrase in lowered
        for phrase in ("username or password invalid", "path: [/ws/", "tls disabled")
    ):
        return True
    if container == "rsshub-redis-1" and any(
        phrase in lowered for phrase in ("bf-error-rate", "search> ", "memory overcommit")
    ):
        return True
    return container == "fleetge" and "agent poll failed" in lowered


def classify_line(
    container: str,
    line: str,
    default_error: int,
    default_warning: int,
) -> RuleDecision | None:
    normalized = normalize_log_line(line)
    if not normalized or _ignored(container, normalized):
        return None
    container = container.casefold()
    plugin_failure = _notify_hub_plugin_failure(container, normalized, default_error)
    if plugin_failure is not None:
        return plugin_failure
    del default_warning
    if OUT_OF_MEMORY_RE.search(normalized):
        return RuleDecision("process_out_of_memory", "critical", 1)
    if SEGFAULT_RE.search(normalized):
        return RuleDecision("process_segmentation_fault", "critical", 1)
    if STORAGE_FULL_RE.search(normalized):
        return RuleDecision("storage_exhausted", "critical", 1)
    if READ_ONLY_RE.search(normalized):
        return RuleDecision("storage_read_only", "critical", 1)
    if CORRUPTION_RE.search(normalized):
        return RuleDecision("storage_corruption", "critical", 1)
    if SUPERVISOR_RE.search(normalized):
        return RuleDecision("supervisor_unavailable", "critical", 2)
    return None
