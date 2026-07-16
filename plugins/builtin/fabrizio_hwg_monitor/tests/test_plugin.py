from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.builtin.fabrizio_hwg_monitor.plugin import FabrizioHwgPlugin
from plugins.builtin.fabrizio_hwg_monitor.schemas import STATE_KEY, EventDraft
from plugins.shared.x_monitor.models import XPost


class FakeMedia:
    def __init__(self) -> None:
        self.published: list[str] = []

    def public_static_url(self, path: str) -> str:
        return f"https://notify.example.com/{path}"

    async def publish_image_url(
        self, source_url: str, *, retention_seconds: int | None = None
    ) -> str:
        self.published.append(source_url)
        if "fail" in source_url:
            raise RuntimeError("Download failed")
        return f"https://notify.example.com/public/media/mocked_{source_url.split('/')[-1]}"


class FakeContext:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        state: Mapping[str, Any] | None = None,
        receipt_status: str = "accepted",
    ) -> None:
        self.config = dict(config)
        self.states: dict[str, Any] = {STATE_KEY: dict(state)} if state else {}
        self.receipt_status = receipt_status
        self.events: list[EventDraft] = []
        self.saved: list[Any] = []
        self.media = FakeMedia()
        self.logger = MagicMock()

    async def get_config(self) -> Mapping[str, Any]:
        return self.config

    async def get_state(self, key: str, default: Any = None) -> Any:
        return self.states.get(key, default)

    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int:
        self.states[key] = value
        self.saved.append(value)
        return 1

    async def get_secret(self, name: str) -> str:
        return "fake-secret"

    async def emit_event(self, event: Any) -> Any:
        self.events.append(event)
        return MagicMock(status=self.receipt_status)


@pytest.mark.asyncio
async def test_plugin_baseline_mode() -> None:
    source = MagicMock()
    # 2 tweets
    t1 = XPost(
        id="101",
        author_username="FabrizioRomano",
        text="Here we go 1",
        url="https://x.com/FabrizioRomano/status/101",
        published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    t2 = XPost(
        id="102",
        author_username="FabrizioRomano",
        text="Here we go 2",
        url="https://x.com/FabrizioRomano/status/102",
        published_at=datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC),
    )
    source.fetch = AsyncMock(return_value=[t1, t2])

    plugin = FabrizioHwgPlugin(source=source)
    context = FakeContext(
        config={
            "enabled": True,
            "first_run_mode": "baseline",
        }
    )

    # First run in baseline mode
    result = await plugin.run(context)
    assert result.status == "baseline_initialized"
    assert result.fetched_posts == 2
    assert result.emitted_events == 0

    # State should have saved the latest post ID as cursor
    state = context.states[STATE_KEY]
    assert state["last_seen_post_id"] == "102"
    assert "101" in state["recent_processed_ids"]
    assert "102" in state["recent_processed_ids"]


@pytest.mark.asyncio
async def test_plugin_incremental_run_matches_and_emits() -> None:
    source = MagicMock()
    # Initial state with cursor at 102
    initial_state = {
        "last_seen_post_id": "102",
        "last_seen_published_at": "2026-01-01T12:05:00+00:00",
        "recent_processed_ids": ["101", "102"],
    }

    t3 = XPost(
        id="103",
        author_username="FabrizioRomano",
        text="Regular news here",
        url="https://x.com/FabrizioRomano/status/103",
        published_at=datetime(2026, 1, 1, 12, 10, 0, tzinfo=UTC),
    )
    t4 = XPost(
        id="104",
        author_username="FabrizioRomano",
        text="João Pedro to Chelsea, HERE WE GO! 🚨 Done deal.",
        url="https://x.com/FabrizioRomano/status/104",
        published_at=datetime(2026, 1, 1, 12, 15, 0, tzinfo=UTC),
        photo_urls=["https://pbs.twimg.com/media/player.jpg"],
    )
    source.fetch = AsyncMock(return_value=[t3, t4])

    plugin = FabrizioHwgPlugin(source=source)
    context = FakeContext(
        config={
            "enabled": True,
            "fallback_cover_url": "https://notify.example.com/fallback.png",
        },
        state=initial_state,
    )

    result = await plugin.run(context)
    assert result.status == "success"
    assert result.fetched_posts == 2
    assert result.new_posts == 2
    assert result.matched_posts == 1
    assert result.emitted_events == 1

    # Should have published the cover image
    assert "https://pbs.twimg.com/media/player.jpg" in context.media.published

    # Check emitted event content
    assert len(context.events) == 1
    event = context.events[0]
    assert event.event_type == "football.transfer_here_we_go"
    assert event.title == "🚨 HERE WE GO｜Fabrizio Romano"
    assert event.content == "João Pedro to Chelsea, HERE WE GO! 🚨 Done deal."
    assert str(event.image_url) == "https://notify.example.com/public/media/mocked_player.jpg"
    assert str(event.url) == "https://x.com/FabrizioRomano/status/104"

    # State cursor should be updated to 104
    state = context.states[STATE_KEY]
    assert state["last_seen_post_id"] == "104"


@pytest.mark.asyncio
async def test_plugin_cover_proxy_failure_falls_back() -> None:
    source = MagicMock()
    initial_state = {
        "last_seen_post_id": "102",
        "last_seen_published_at": "2026-01-01T12:05:00+00:00",
        "recent_processed_ids": ["101", "102"],
    }

    t3 = XPost(
        id="103",
        author_username="FabrizioRomano",
        text="Chelsea deal HWG",
        url="https://x.com/FabrizioRomano/status/103",
        published_at=datetime(2026, 1, 1, 12, 10, 0, tzinfo=UTC),
        photo_urls=["https://pbs.twimg.com/media/fail.jpg"],  # Trigger fail mock
    )
    source.fetch = AsyncMock(return_value=[t3])

    plugin = FabrizioHwgPlugin(source=source)
    context = FakeContext(
        config={
            "enabled": True,
            "fallback_cover_url": "https://notify.example.com/fallback.png",
        },
        state=initial_state,
    )

    result = await plugin.run(context)
    assert result.status == "success"
    assert result.emitted_events == 1

    # Check emitted event falls back to fallback cover
    assert len(context.events) == 1
    event = context.events[0]
    assert str(event.image_url) == "https://notify.example.com/fallback.png"
    assert context.states[STATE_KEY]["last_seen_post_id"] == "103"
