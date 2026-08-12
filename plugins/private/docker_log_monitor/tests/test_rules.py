from __future__ import annotations

import json
from pathlib import Path

from plugins.private.docker_log_monitor.rules import CONTAINER_PROFILES, classify_line

FIXTURE = Path(__file__).parent / "fixtures" / "log_cases.json"
CURRENT_CONTAINER_NAMES = {
    "notify-hub",
    "cli-proxy-api",
    "rsshub-rsshub-1",
    "rsshub-redis-1",
    "cloud-media-sync",
    "fleetge",
    "fleetge-agent",
    "fb3",
    "mdc",
    "emby_cms",
    "rsshub-browserless-1",
    "vaultwarden",
    "superng6_qbittorrent",
    "family-finance",
    "moviepilot_115",
    "moviepilot_v2",
    "readflow-postgres",
    "readflow-server",
    "linuxserver_transmission",
    "minio",
    "lucky",
    "homepage",
    "alist",
    "qbittorrent",
    "ota-analyzer",
    "asset-manage-asset-manage-1",
    "dailyreport-dashboard-1",
    "obsidian-sync-db",
    "issue-analyzer-issue-analyzer-1",
    "wxchat",
    "it-tools",
}


def test_every_observed_container_has_an_explicit_profile() -> None:
    assert set(CONTAINER_PROFILES) == CURRENT_CONTAINER_NAMES


def test_observed_log_shapes_match_expected_rules() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in cases:
        decision = classify_line(case["container"], case["line"], 3, 5)
        if case["rule"] is None:
            assert decision is None, case
            continue
        assert decision is not None, case
        assert decision.rule_id == case["rule"]
        assert decision.threshold == case["threshold"]


def test_unknown_container_uses_configured_defaults() -> None:
    assert classify_line("new-service", "ERROR upstream failed", 7, 9) is None
    assert classify_line("new-service", "WARNING retry timeout", 7, 9) is None


def test_notify_hub_plugin_failure_fingerprint_ignores_run_id_and_timestamp() -> None:
    first = classify_line(
        "notify-hub",
        '2026-08-09T00:30:13Z {"plugin_id":"codex_x_monitor",'
        '"plugin_run_id":"prun_first","error_type":"AIGatewayError",'
        '"event":"plugin run failed","level":"error",'
        '"timestamp":"2026-08-09T00:30:13Z"}',
        7,
        9,
    )
    second = classify_line(
        "notify-hub",
        '2026-08-09T00:32:08Z {"plugin_id":"codex_x_monitor",'
        '"plugin_run_id":"prun_second","error_type":"AIGatewayError",'
        '"event":"plugin run failed","level":"error",'
        '"timestamp":"2026-08-09T00:32:08Z"}',
        7,
        9,
    )

    assert first is not None
    assert second is not None
    assert first.threshold == CONTAINER_PROFILES["notify-hub"].error_threshold
    assert first.fingerprint == second.fingerprint == "codex_x_monitor:aigatewayerror"
