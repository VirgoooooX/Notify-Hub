from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from plugins.private.docker_log_monitor.schemas import HostTarget
from plugins.private.docker_log_monitor.source import scan_host, scan_since


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    text: str = ""

    def json(self) -> Any:
        return self.payload


class FakeHttp:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class FakeContext:
    def __init__(self, responses: list[FakeResponse], secret: str) -> None:
        self.http = FakeHttp(responses)
        self.secret = secret

    async def get_secret(self, name: str) -> str:
        assert name == "istoreos_agent"
        return self.secret


@pytest.mark.asyncio
async def test_scan_host_reads_container_state_and_incremental_logs() -> None:
    context = FakeContext(
        [
            FakeResponse(200, [{"Id": "abc123", "Names": ["/notify-hub"]}]),
            FakeResponse(
                200,
                {
                    "RestartCount": 2,
                    "State": {
                        "Running": True,
                        "Status": "running",
                        "OOMKilled": False,
                        "Health": {"Status": "healthy"},
                    },
                },
            ),
            FakeResponse(200, text="2026-07-28T12:00:00Z ERROR example"),
        ],
        '{"base_url":"http://192.168.31.100:8080/private","token":"hidden-token"}',
    )
    result = await scan_host(context, HostTarget(host_id="istoreos"), since=12345, log_tail=2000)
    assert result.error is None
    assert result.containers[0].restart_count == 2
    assert result.containers[0].health == "healthy"
    assert context.http.calls[2]["params"]["since"] == "12345"
    assert context.http.calls[0]["headers"]["Authorization"] == "Bearer hidden-token"


@pytest.mark.asyncio
async def test_source_error_never_includes_agent_token() -> None:
    secret = '{"base_url":"https://metrics.1989009.xyz/private","token":"never-leak-me"}'
    context = FakeContext([FakeResponse(401)], secret)
    context.get_secret = lambda _name: _async_value(secret)  # type: ignore[method-assign]
    result = await scan_host(context, HostTarget(host_id="oc-chicago"), since=12345, log_tail=2000)
    assert result.error == "container list returned HTTP 401"
    assert "never-leak-me" not in result.error


@pytest.mark.asyncio
async def test_container_timeout_is_partial_and_does_not_fail_host_scan() -> None:
    context = FakeContext(
        [
            FakeResponse(200, [{"Id": "abc123", "Names": ["/app"]}]),
            FakeResponse(200, {"RestartCount": 0, "State": {"Running": True}}),
        ],
        '{"base_url":"http://192.168.31.100:8080/private","token":"hidden-token"}',
    )

    async def get_with_timeout(url: str, **kwargs: Any) -> FakeResponse:
        context.http.calls.append({"url": url, **kwargs})
        if url.endswith("/logs"):
            raise TimeoutError
        return context.http.responses.pop(0)

    context.http.get = get_with_timeout  # type: ignore[method-assign]
    result = await scan_host(context, HostTarget(host_id="istoreos"), since=12345, log_tail=2000)

    assert result.error is None
    assert result.containers == []
    assert result.partial_errors == ["app: request failed: TimeoutError"]


async def _async_value(value: str) -> str:
    return value


def test_scan_since_uses_bounded_overlap() -> None:
    last_scan = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert scan_since(last_scan, 600) == int(last_scan.timestamp()) - 60
    assert scan_since(None, 600) is None
