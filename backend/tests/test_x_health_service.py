from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.application.event_service import EventService
from app.application.x_health_service import XHealthService
from app.application.x_source_service import XHealthTarget, XSourceFetchResult
from app.config import Settings
from app.domain.x_source import (
    XPostRecord,
    XSourceAccountError,
    XSourceCookieInvalid,
    XSourcesUnavailable,
    XSourceUnavailable,
)
from app.infrastructure.database import Base
from app.infrastructure.database.models import Delivery, Event, Notification, XSourceHealth
from app.infrastructure.database.session import create_session_factory
from app.workers.x_health_worker import XHealthWorker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FakeSource:
    provider_name = "rsshub"

    def __init__(self, values: list[Any]) -> None:
        self.values = list(values)

    async def fetch_records(
        self, _username: str, *, limit: int, include_replies: bool
    ) -> list[Any]:
        del limit, include_replies
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeStatusSource(FakeSource):
    async def fetch_with_status(
        self, _username: str, *, limit: int, include_replies: bool
    ) -> XSourceFetchResult:
        del limit, include_replies
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def accept_internal_event(self, **values: Any) -> SimpleNamespace:
        self.events.append(values)
        return SimpleNamespace(duplicate=False)


def post(published_at: datetime | None = None) -> XPostRecord:
    return XPostRecord(
        id="2002",
        author_username="FabrizioRomano",
        text="HERE WE GO",
        url="https://x.com/FabrizioRomano/status/2002",
        published_at=published_at or datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_health_alerts_once_after_threshold_and_notifies_recovery(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'health.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_health_failure_threshold=2,
        x_health_repeat_interval_seconds=300,
        x_health_recovery_success_threshold=2,
        x_health_alert_recipient_ids=["person_admin"],
    )
    clock = FakeClock()
    events = FakeEvents()
    source = FakeSource(
        [
            XSourceUnavailable("down"),
            XSourceUnavailable("down"),
            XSourceUnavailable("down"),
            XSourceUnavailable("down"),
            [post()],
            [post()],
        ]
    )

    async def targets() -> list[XHealthTarget]:
        return [XHealthTarget(plugin_id="fabrizio_hwg_monitor", username="FabrizioRomano")]

    service = XHealthService(factory, clock, events, source, targets, settings)  # type: ignore[arg-type]

    assert await service.run_once() == 0
    assert await service.run_once() == 1
    assert [event["event_type"] for event in events.events] == ["system.x_source_unavailable"]
    assert await service.run_once() == 0
    clock.value += timedelta(seconds=301)
    assert await service.run_once() == 1
    assert len(events.events) == 2
    assert await service.run_once() == 0
    assert await service.run_once() == 1
    assert [event["event_type"] for event in events.events] == [
        "system.x_source_unavailable",
        "system.x_source_unavailable",
        "system.x_source_recovered",
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_error_does_not_create_provider_incident(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'account.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_health_failure_threshold=1,
        x_health_alert_recipient_ids=["person_admin"],
    )
    events = FakeEvents()
    source = FakeSource([XSourceAccountError("missing account")])

    async def targets() -> list[XHealthTarget]:
        return [XHealthTarget(plugin_id="codex_x_monitor", username="missing")]

    service = XHealthService(factory, FakeClock(), events, source, targets, settings)  # type: ignore[arg-type]

    assert await service.run_once() == 1
    assert [event["event_type"] for event in events.events] == ["system.x_account_unavailable"]
    async with factory() as session:
        rows = list(await session.scalars(select(XSourceHealth)))
    assert {row.scope_type for row in rows} == {"account", "provider"}
    assert next(row for row in rows if row.scope_type == "provider").status == "healthy"
    assert next(row for row in rows if row.scope_type == "account").status == "incident"
    await engine.dispose()


@pytest.mark.asyncio
async def test_primary_degradation_alerts_and_records_rsshub_fallback(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'degraded.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_source_provider="twscrape",
        x_twscrape_cookie="auth_token=test; ct0=test",
        x_health_alert_recipient_ids=["person_admin"],
        x_health_recovery_success_threshold=1,
    )
    events = FakeEvents()
    source = FakeStatusSource(
        [
            XSourceFetchResult(
                records=[post()],
                provider_used="rsshub",
                degraded_error=XSourceCookieInvalid("Cookie rejected"),
            ),
            XSourceFetchResult(records=[post()], provider_used="twscrape"),
        ]
    )

    async def targets() -> list[XHealthTarget]:
        return [XHealthTarget(plugin_id="codex_x_monitor", username="thsottiaux")]

    service = XHealthService(factory, FakeClock(), events, source, targets, settings)  # type: ignore[arg-type]

    assert await service.run_once() == 1
    assert events.events[0]["event_type"] == "system.x_source_degraded"
    assert events.events[0]["payload"]["error_code"] == "x_source_cookie_invalid"
    assert events.events[0]["payload"]["provider_used"] == "rsshub"
    assert await service.run_once() == 1
    assert events.events[1]["event_type"] == "system.x_source_recovered"
    await engine.dispose()


@pytest.mark.asyncio
async def test_both_sources_unavailable_alerts_without_waiting_for_threshold(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'both-down.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_health_failure_threshold=3,
        x_health_alert_recipient_ids=["person_admin"],
    )
    events = FakeEvents()
    source = FakeSource(
        [XSourcesUnavailable(XSourceCookieInvalid("bad"), XSourceUnavailable("down"))]
    )

    async def targets() -> list[XHealthTarget]:
        return [XHealthTarget(plugin_id="codex_x_monitor", username="thsottiaux")]

    service = XHealthService(factory, FakeClock(), events, source, targets, settings)  # type: ignore[arg-type]

    assert await service.run_once() == 1
    assert events.events[0]["event_type"] == "system.x_source_unavailable"
    assert events.events[0]["payload"]["error_code"] == "x_sources_unavailable"
    await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_account_and_provider_failures_still_alert_provider(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'mixed.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_health_failure_threshold=1,
        x_health_alert_recipient_ids=["person_admin"],
    )
    events = FakeEvents()
    source = FakeSource([XSourceAccountError("missing account"), XSourceUnavailable("down")])

    async def targets() -> list[XHealthTarget]:
        return [
            XHealthTarget(plugin_id="codex_x_monitor", username="missing"),
            XHealthTarget(plugin_id="fabrizio_hwg_monitor", username="FabrizioRomano"),
        ]

    service = XHealthService(factory, FakeClock(), events, source, targets, settings)  # type: ignore[arg-type]

    assert await service.run_once() == 1
    assert [event["event_type"] for event in events.events] == ["system.x_source_unavailable"]
    async with factory() as session:
        rows = list(await session.scalars(select(XSourceHealth)))
    assert next(row for row in rows if row.scope_type == "provider").status == "incident"
    assert sum(row.status == "incident" for row in rows if row.scope_type == "account") == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_health_alert_persists_event_notification_and_delivery_and_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'delivery.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_health_failure_threshold=1,
        x_health_alert_recipient_ids=["person_admin"],
    )
    clock = FakeClock()
    source = FakeSource([XSourceUnavailable("down"), XSourceUnavailable("down")])

    async def targets() -> list[XHealthTarget]:
        return [XHealthTarget(plugin_id="fabrizio_hwg_monitor", username="FabrizioRomano")]

    service = XHealthService(
        factory,
        clock,
        EventService(factory, clock),
        source,
        targets,
        settings,
    )  # type: ignore[arg-type]

    assert await service.run_once() == 1
    # The same incident and repeat bucket must be accepted as a duplicate,
    # without creating a second notification or delivery.
    assert await service.run_once() == 0
    async with factory() as session:
        events = list(await session.scalars(select(Event)))
        notifications = list(await session.scalars(select(Notification)))
        deliveries = list(await session.scalars(select(Delivery)))
    assert len(events) == len(notifications) == len(deliveries) == 1
    assert deliveries[0].recipient_id == "person_admin"
    assert deliveries[0].status == "pending"
    await engine.dispose()


@pytest.mark.asyncio
async def test_content_silence_alerts_on_empty_feed_and_recovers(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'stale.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        x_health_content_silence_enabled=True,
        x_health_content_silence_seconds=3_600,
        x_health_alert_recipient_ids=["person_admin"],
    )
    clock = FakeClock()
    events = FakeEvents()
    source = FakeSource(
        [
            [post(datetime(2026, 8, 25, 8, 45, tzinfo=UTC))],
            [],
            [post(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))],
        ]
    )

    async def targets() -> list[XHealthTarget]:
        return [
            XHealthTarget(
                plugin_id="fabrizio_hwg_monitor",
                username="FabrizioRomano",
                silence_enabled=True,
                silence_seconds=3_600,
            )
        ]

    service = XHealthService(factory, clock, events, source, targets, settings)  # type: ignore[arg-type]

    assert await service.run_once() == 0
    clock.value = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    assert await service.run_once() == 1
    assert events.events[-1]["event_type"] == "system.x_account_content_stale"
    clock.value = datetime(2026, 8, 25, 10, 30, tzinfo=UTC)
    assert await service.run_once() == 1
    assert events.events[-1]["event_type"] == "system.x_account_content_recovered"
    await engine.dispose()


@pytest.mark.asyncio
async def test_health_worker_survives_one_iteration_failure_and_stops_cleanly() -> None:
    class FlakyService:
        def __init__(self) -> None:
            self.calls = 0

        async def run_once(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic failure")
            return 1

    service = FlakyService()
    worker = XHealthWorker(service, poll_seconds=3600)  # type: ignore[arg-type]
    assert await worker.run_once() == 0
    assert await worker.run_once() == 1
    await worker.start()
    await asyncio.sleep(0)
    await worker.stop()
    assert service.calls >= 3
