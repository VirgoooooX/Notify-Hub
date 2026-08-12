from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from app.api.admin_management import SettingsUpdate
from app.api.client_reminders import ClientReminderSchedule
from app.api.reminders import PreviewInput, ScheduleInput
from app.api.schemas import EventCreate
from app.api.wecom_mobile import MobileScheduleInput
from app.application.platform_settings import read_platform_timezone
from app.application.time_utils import ensure_utc, resolve_datetime
from app.config import Settings
from app.infrastructure.database import Base
from app.infrastructure.database.models import PlatformSetting, WorkerHeartbeat
from app.infrastructure.database.utc_datetime import UTCDateTime
from app.plugin_runtime.context import PluginReminderClient
from sqlalchemy import DateTime, select
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_core_api import initialize_and_login


def test_instant_requires_offset_and_normalizes_to_utc() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        ensure_utc(datetime(2026, 1, 1), field="occurred_at")
    value = ensure_utc(
        datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))), field="occurred_at"
    )
    assert value == datetime(2026, 1, 1, tzinfo=UTC)


def test_local_dst_gap_is_rejected_and_fold_uses_first_occurrence() -> None:
    with pytest.raises(ValueError, match="valid local time"):
        resolve_datetime(datetime(2026, 3, 8, 2, 30), "America/New_York", field="schedule.at")
    first = resolve_datetime(datetime(2026, 11, 1, 1, 30), "America/New_York")
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_schedule_wall_time_and_event_instant_contracts() -> None:
    schedule = ScheduleInput(type="once", at="2026-11-01T01:30:00", timezone="America/New_York")
    assert schedule.at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    legacy_instant = ScheduleInput(
        type="once", at="2026-11-01T01:30:00-05:00", timezone="America/New_York"
    )
    assert legacy_instant.at == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone offset"):
        EventCreate(
            event_type="test",
            event_key="key",
            title="title",
            occurred_at="2026-01-01T00:00:00",
            recipients=["person_1"],
        )


def test_omitted_schedule_timezone_is_resolved_at_request_boundary() -> None:
    admin = ScheduleInput(type="once", at="2026-11-01T01:30:00")
    admin.normalize_timezone("America/New_York")
    assert admin.timezone == "America/New_York"
    assert admin.at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

    client = ClientReminderSchedule(type="once", at="2026-11-01T01:30:00")
    client.normalize_timezone("America/New_York")
    assert client.timezone == "America/New_York"
    assert client.at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

    mobile = MobileScheduleInput(at="2026-11-01T01:30:00")
    mobile.normalize_timezone("America/New_York")
    assert mobile.timezone == "America/New_York"
    assert mobile.at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_plugin_client_uses_injected_platform_timezone() -> None:
    captured: list[Any] = []

    class Creator:
        async def create(self, draft: Any) -> Any:
            captured.append(draft)
            return type("Receipt", (), {"reminder_id": "rem_1"})()

    result = await PluginReminderClient(Creator(), default_timezone="America/New_York").create(
        creator_person_id="person_1",
        title="Plugin reminder",
        schedule_type="once",
        scheduled_at="2026-11-01T01:30:00",
        recipient_ids=("person_1",),
    )
    assert result.reminder_id == "rem_1"
    assert captured[0].timezone == "America/New_York"
    assert captured[0].scheduled_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_schedule_preview_resolves_wall_time_in_selected_timezone() -> None:
    preview = PreviewInput(
        type="cron",
        cron_expression="30 9 * * *",
        timezone="America/New_York",
        start_at="2026-07-16T09:30:00",
        end_at="2026-07-17T09:30:00",
    )

    assert preview.start_at == datetime(2026, 7, 16, 13, 30, tzinfo=UTC)
    assert preview.end_at == datetime(2026, 7, 17, 13, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sqlite_datetime_round_trip_is_aware_utc(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'time.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    value = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    async with factory() as session, session.begin():
        session.add(
            WorkerHeartbeat(
                worker_id="time-test",
                worker_type="test",
                heartbeat_at=value,
            )
        )
    async with factory() as session:
        row = await session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "time-test")
        )
    assert row is not None
    assert row.heartbeat_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert row.heartbeat_at.tzinfo is UTC
    with pytest.raises(StatementError, match="include timezone"):
        async with factory() as session, session.begin():
            session.add(
                WorkerHeartbeat(
                    worker_id="naive-test",
                    worker_type="test",
                    heartbeat_at=datetime(2026, 1, 1),
                )
            )
    await engine.dispose()


def test_settings_reject_invalid_platform_timezone() -> None:
    with pytest.raises(ValueError, match="IANA timezone"):
        Settings(_env_file=None, app_timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="IANA timezone"):
        Settings(_env_file=None, log_timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="must not be null"):
        SettingsUpdate(timezone=None)


def test_all_persisted_datetime_columns_use_utc_type_without_ddl_change() -> None:
    datetime_columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, UTCDateTime)
    ]
    assert datetime_columns
    assert all(isinstance(column.type, UTCDateTime) for column in datetime_columns)
    assert not [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if type(column.type) is DateTime
    ]
    assert str(UTCDateTime().compile(dialect=sqlite_dialect())).upper() == "DATETIME"


@pytest.mark.asyncio
async def test_platform_timezone_reads_persisted_value_immediately(api: tuple[Any, Any]) -> None:
    _client, app = api
    assert (
        await read_platform_timezone(app.state.session_factory, app.state.settings.app_timezone)
        == app.state.settings.app_timezone
    )
    now = app.state.clock.now()
    async with app.state.session_factory() as session, session.begin():
        session.add(
            PlatformSetting(
                key="timezone",
                value="America/New_York",
                created_at=now,
                updated_at=now,
            )
        )
    assert await read_platform_timezone(app.state.session_factory, "UTC") == "America/New_York"


@pytest.mark.asyncio
async def test_admin_reminder_uses_runtime_default_and_update_keeps_frozen_timezone(
    api: tuple[Any, Any],
) -> None:
    client, _app = api
    token = await initialize_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    person = await client.post(
        "/api/v1/admin/people",
        headers=headers,
        json={"id": "person_timezone", "display_name": "Timezone Person"},
    )
    assert person.status_code == 201, person.text
    settings = await client.patch(
        "/api/v1/admin/settings",
        headers=headers,
        json={"timezone": "America/New_York"},
    )
    assert settings.status_code == 200, settings.text

    created = await client.post(
        "/api/v1/admin/reminders",
        headers=headers,
        json={
            "title": "Runtime timezone",
            "schedule": {"type": "once", "at": "2026-11-01T01:30:00"},
            "recipients": ["person_timezone"],
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()["data"]
    assert item["timezone"] == "America/New_York"
    assert datetime.fromisoformat(item["next_run_at"]) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

    changed_default = await client.patch(
        "/api/v1/admin/settings",
        headers=headers,
        json={"timezone": "Europe/Berlin"},
    )
    assert changed_default.status_code == 200, changed_default.text
    updated = await client.patch(
        f"/api/v1/admin/reminders/{item['id']}",
        headers=headers,
        json={"schedule": {"type": "once", "at": "2026-11-02T01:30:00"}},
    )
    assert updated.status_code == 200, updated.text
    updated_item = updated.json()["data"]
    assert updated_item["timezone"] == "America/New_York"
    assert datetime.fromisoformat(updated_item["next_run_at"]) == datetime(
        2026, 11, 2, 6, 30, tzinfo=UTC
    )
