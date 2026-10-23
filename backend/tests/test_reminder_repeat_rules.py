import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from app.api.reminder_repeat import RepeatInput
from app.application.reminder_access import ReminderAccessDenied, ReminderActor, ReminderPermissions
from app.application.reminder_service import ReminderCreate, ReminderService, ReminderUpdate
from app.application.runtime_adapters import ReminderEventEmitterAdapter
from app.domain.reminder_repeat import RepeatRule, due_repeat_start, next_repeat_at
from app.domain.reminders import InvalidReminderTransition, ReminderError, ScheduleType
from app.infrastructure.database.models import Delivery, Event, WeComIdentity
from app.infrastructure.database.reminder_models import (
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from app.workers.reminder_worker import ReminderWorker
from sqlalchemy import func, select

from tests.test_core_api import initialize_and_login
from tests.test_reminder_reliability import _add_person

START = datetime(2030, 1, 1, 1, tzinfo=UTC)


def command(person: str = "repeat_person", **updates: Any) -> ReminderCreate:
    return replace(
        ReminderCreate(
            creator_person_id=person,
            title="Server expiry",
            content="Stop this round if unneeded",
            schedule_type=ScheduleType.ONCE,
            timezone="Asia/Shanghai",
            recipient_ids=(person,),
            scheduled_at=START,
            require_ack=True,
            repeat_config={
                "type": "before_due",
                "due_at": (START + timedelta(days=30)).isoformat(),
                "days_before_due": [30, 7, 3, 1],
            },
        ),
        **updates,
    )


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "offsets", "offsets_seconds": [0, 0, 300]},
        {"type": "offsets", "offsets_seconds": [300, 600]},
        {"type": "offsets", "offsets_seconds": [0, 299]},
        {"type": "offsets", "offsets_seconds": [0, 31 * 86400]},
        {"type": "before_due", "due_at": START, "days_before_due": [1, 7]},
        {"type": "before_due", "due_at": START, "days_before_due": [30, 30]},
        {"type": "before_due", "due_at": START, "days_before_due": [-1]},
        {"type": "cron", "cron_expression": "0 9 * * *", "interval_seconds": 300},
    ],
)
def test_invalid_repeat_rules_are_rejected(rule: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RepeatInput.model_validate(rule)


def test_fixed_nodes_do_not_drift_and_creation_skips_past_nodes() -> None:
    rule = RepeatRule.model_validate(command().repeat_config)
    assert due_repeat_start(rule, now=START + timedelta(days=10)) == START + timedelta(days=23)
    assert next_repeat_at(
        {"type": "offsets", "offsets_seconds": [0, 86400, 3 * 86400]},
        interval_seconds=None,
        scheduled_for=START,
        after=START + timedelta(days=2),
        timezone="UTC",
    ) == START + timedelta(days=3)


@pytest.mark.asyncio
async def test_expiry_nodes_are_one_round_and_survive_service_restart(api: tuple[Any, Any]) -> None:
    _, app = api
    await _add_person(app, "repeat_person")
    service = app.state.reminder_service
    reminder = await service.create(command(), now=START)
    worker = ReminderWorker(service)
    assert await worker.run_once(now=START) == 1
    assert await worker.run_once(now=START + timedelta(days=1)) == 0
    # A fresh service/worker must recover the rule and the next node from SQLite.
    service = ReminderService(
        app.state.session_factory, ReminderEventEmitterAdapter(app.state.event_service), "test"
    )
    worker = ReminderWorker(service)
    for day in (23, 27, 29):
        assert await worker.run_once(now=START + timedelta(days=day)) == 1
        assert await worker.run_once(now=START + timedelta(days=day)) == 0
    assert await worker.run_once(now=START + timedelta(days=29, hours=1)) == 0
    async with app.state.session_factory() as session:
        rounds = list(
            await session.scalars(
                select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder.id)
            )
        )
        assert len(rounds) == 1
        assert rounds[0].require_ack_snapshot
        recipient = await session.scalar(
            select(ReminderOccurrenceRecipient).where(
                ReminderOccurrenceRecipient.occurrence_id == rounds[0].id
            )
        )
        assert recipient is not None and recipient.notify_count == 4
        assert (
            await session.scalar(select(func.count(Event.id)).where(Event.source_id == reminder.id))
            == 4
        )


@pytest.mark.asyncio
async def test_stop_cancels_remaining_nodes_but_not_other_servers_or_new_cycles(
    api: tuple[Any, Any],
) -> None:
    _, app = api
    await _add_person(app, "repeat_person", wecom_user_id="repeat_user")
    service = app.state.reminder_service
    first = await service.create(command(), now=START)
    other = await service.create(command(title="Other server"), now=START)
    worker = ReminderWorker(service)
    assert await worker.run_once(now=START) == 2
    async with app.state.session_factory() as session, session.begin():
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == first.id)
        )
        assert occurrence is not None
        identity = await session.scalar(
            select(WeComIdentity).where(WeComIdentity.user_id == "repeat_user")
        )
        assert identity is not None
        identity.latest_interactive_occurrence_id = occurrence.id
    deferred = await service.operate_latest_interactive(
        sender_wecom_userid="repeat_user", operation="snooze_10", now=START
    )
    assert deferred.code == "snooze_10"
    assert await worker.run_once(now=START + timedelta(minutes=10)) == 0
    result = await service.operate_latest_interactive(
        sender_wecom_userid="repeat_user", operation="stop", now=START
    )
    assert result.code == "stopped"
    assert await worker.run_once(now=START + timedelta(days=23)) == 1
    async with app.state.session_factory() as session:
        assert (await session.get(ReminderOccurrence, occurrence.id)).status == "cancelled"
        assert (
            await session.scalar(select(func.count(Event.id)).where(Event.source_id == first.id))
            == 1
        )
        assert (
            await session.scalar(select(func.count(Event.id)).where(Event.source_id == other.id))
            == 2
        )
        assert set(
            await session.scalars(
                select(Delivery.status).where(Delivery.recipient_id == "repeat_person")
            )
        ) >= {"cancelled"}
    saved = await service.get(first.id)
    assert datetime.fromisoformat(saved.repeat_config["due_at"]) == START + timedelta(days=30)
    with pytest.raises(InvalidReminderTransition):
        await service.update(
            first.id, ReminderUpdate(title="Cannot reopen stopped cycle"), now=START
        )
    next_start = START + timedelta(days=365)
    await service.create(
        command(
            repeat_config={
                "type": "before_due",
                "due_at": (next_start + timedelta(days=30)).isoformat(),
                "days_before_due": [30, 7, 3, 1],
            }
        ),
        now=next_start,
    )
    assert await worker.run_once(now=next_start) == 1


@pytest.mark.asyncio
async def test_cron_is_inside_one_round_and_uses_frozen_timezone(api: tuple[Any, Any]) -> None:
    _, app = api
    await _add_person(app, "repeat_person")
    service = app.state.reminder_service
    reminder = await service.create(
        command(repeat_config={"type": "cron", "cron_expression": "0 9 * * *"}), now=START
    )
    worker = ReminderWorker(service)
    assert await worker.run_once(now=START) == 1
    assert await worker.run_once(now=START + timedelta(hours=23)) == 0
    assert await worker.run_once(now=START + timedelta(days=1)) == 1
    async with app.state.session_factory() as session:
        rounds = list(
            await session.scalars(
                select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder.id)
            )
        )
        assert len(rounds) == 1
        assert rounds[0].repeat_config_snapshot["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_cron_permissions_and_node_duration_are_enforced(api: tuple[Any, Any]) -> None:
    _, app = api
    perms = ReminderPermissions(
        allow_create=True,
        allow_interactive=True,
        allowed_recipients=("repeat_person",),
        max_active=5,
    )
    with pytest.raises(ReminderAccessDenied, match="cron"):
        await app.state.reminder_access_service.create(
            command(repeat_config={"type": "cron", "cron_expression": "0 9 * * *"}),
            actor=ReminderActor("plugin", "test"),
            permissions=perms,
        )
    with pytest.raises(ReminderAccessDenied, match="duration"):
        await app.state.reminder_access_service.create(
            command(), actor=ReminderActor("plugin", "test"), permissions=perms
        )


@pytest.mark.asyncio
async def test_invalid_cron_and_noninteractive_rules_return_stable_errors(
    api: tuple[Any, Any],
) -> None:
    _, app = api
    await _add_person(app, "repeat_person")
    for config in (
        {"type": "cron", "cron_expression": "bad"},
        {"type": "cron", "cron_expression": "* * * * *"},
    ):
        with pytest.raises(ReminderError):
            await app.state.reminder_service.create(command(repeat_config=config), now=START)
    with pytest.raises(ReminderError):
        await app.state.reminder_service.create(command(require_ack=False), now=START)


@pytest.mark.asyncio
async def test_admin_api_normalizes_due_time_and_snapshots_edits(api: tuple[Any, Any]) -> None:
    client, app = api
    await _add_person(app, "repeat_person")
    token = await initialize_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "title": "Expiry",
        "schedule": {"type": "once", "at": "2030-01-31T09:00", "timezone": "Asia/Shanghai"},
        "recipients": ["repeat_person"],
        "require_ack": True,
        "repeat": {
            "type": "before_due",
            "due_at": "2030-01-31T09:00",
            "days_before_due": [30, 7, 3, 1],
        },
    }
    response = await client.post("/api/v1/admin/reminders", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert datetime.fromisoformat(data["next_run_at"]) == START
    assert data["repeat_config"]["due_at"].startswith("2030-01-31T01:00:00")
    assert await ReminderWorker(app.state.reminder_service).run_once(now=START) == 1
    response = await client.patch(
        f"/api/v1/admin/reminders/{data['id']}",
        headers=headers,
        json={"repeat": {"type": "offsets", "offsets_seconds": [0, 3600]}},
    )
    assert response.status_code == 200, response.text
    async with app.state.session_factory() as session:
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == data["id"])
        )
        assert occurrence.repeat_config_snapshot["type"] == "before_due"


@pytest.mark.asyncio
async def test_client_repeat_rules_are_idempotent_and_cron_requires_permission(
    api: tuple[Any, Any],
) -> None:
    client, _ = api
    token = await initialize_and_login(client)
    admin_headers = {"Authorization": f"Bearer {token}"}
    person = await client.post(
        "/api/v1/admin/people",
        headers=admin_headers,
        json={"id": "person_repeat", "display_name": "Server owner"},
    )
    assert person.status_code == 201
    actor = await client.post(
        "/api/v1/admin/api-clients",
        headers=admin_headers,
        json={
            "name": "repeat-client",
            "allowed_recipient_ids": ["person_repeat"],
            "allow_reminders": True,
            "allow_interactive": True,
            "max_active_reminders": 5,
        },
    )
    assert actor.status_code == 201, actor.text
    headers = {"X-API-Key": actor.json()["data"]["api_key"], "Idempotency-Key": "server-cycle-1"}
    body = {
        "title": "Expiry",
        "schedule": {"type": "once", "at": "2030-01-31T09:00", "timezone": "Asia/Shanghai"},
        "recipients": ["person_repeat"],
        "require_ack": True,
        "repeat": {
            "type": "before_due",
            "due_at": "2030-01-31T09:00",
            "days_before_due": [30, 7, 3, 1],
        },
    }
    first = await client.post("/api/v1/reminders", headers=headers, json=body)
    assert first.status_code == 201, first.text
    duplicate = await client.post("/api/v1/reminders", headers=headers, json=body)
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["id"] == first.json()["data"]["id"]
    body["repeat"]["days_before_due"] = [30, 3, 1]
    conflict = await client.post("/api/v1/reminders", headers=headers, json=body)
    assert conflict.status_code == 409
    body["repeat"] = {"type": "cron", "cron_expression": "0 9 * * *"}
    denied = await client.post("/api/v1/reminders", headers=headers, json=body)
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_repeat_timeout_reuses_node_event_key_and_does_not_advance(
    api: tuple[Any, Any],
) -> None:
    _, app = api
    await _add_person(app, "repeat_person")
    service = app.state.reminder_service
    reminder = await service.create(command(), now=START)
    assert await service.claim_due(worker_id="test", now=START) == [reminder.id]
    await service.trigger_claimed(reminder.id, worker_id="test", now=START)
    (recipient_id,) = await service.claim_due_recipients(worker_id="test", now=START)
    emitter = ReminderEventEmitterAdapter(app.state.event_service)
    keys = []

    async def accepted_but_response_lost(draft: Any) -> Any:
        keys.append(draft.event_key)
        await emitter(draft)
        raise TimeoutError("response lost")

    failing = ReminderService(app.state.session_factory, accepted_but_response_lost, "test")
    with pytest.raises(TimeoutError):
        await failing.notify_recipient(recipient_id, worker_id="test", now=START)
    async with app.state.session_factory() as session:
        recipient = await session.get(ReminderOccurrenceRecipient, recipient_id)
        assert recipient.notify_count == 0 and recipient.claimed_by is None
    assert await service.claim_due_recipients(worker_id="retry", now=START) == [recipient_id]
    accepted = await service.notify_recipient(recipient_id, worker_id="retry", now=START)
    assert accepted.duplicate
    async with app.state.session_factory() as session:
        events = list(await session.scalars(select(Event).where(Event.source_id == reminder.id)))
        assert len(events) == 1 and events[0].event_key == keys[0]
        recipient = await session.get(ReminderOccurrenceRecipient, recipient_id)
        assert recipient.notify_count == 1
        assert recipient.next_notify_at == START + timedelta(days=23)


def test_migration_from_empty_and_legacy_occurrence_backfill(tmp_path: Path) -> None:
    database = tmp_path / "repeat-migration.db"
    environment = {
        **os.environ,
        "NOTIFY_HUB_ENVIRONMENT": "test",
        "NOTIFY_HUB_DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
        "NOTIFY_HUB_LOG_DIR": str(tmp_path / "logs"),
        "NOTIFY_HUB_MEDIA_ROOT": str(tmp_path / "media"),
    }

    def migrate(direction: str, target: str) -> None:
        subprocess.run(  # noqa: S603 - fixed local Alembic command and test-owned arguments
            [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", direction, target],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    migrate("upgrade", "head")
    migrate("downgrade", "0020_ai_hub_profile_catalog")
    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    metadata = sa.MetaData()
    people = sa.Table("people", metadata, autoload_with=engine)
    reminders = sa.Table("reminders", metadata, autoload_with=engine)
    occurrences = sa.Table("reminder_occurrences", metadata, autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            people.insert().values(
                id="person_legacy",
                display_name="Legacy",
                active=True,
                is_default=False,
                created_at=START,
                updated_at=START,
            )
        )
        connection.execute(
            reminders.insert().values(
                id="rem_legacy",
                creator_person_id="person_legacy",
                title="Legacy",
                content="",
                schedule_type="once",
                schedule_config={},
                timezone="UTC",
                status="active",
                require_ack=True,
                ack_policy="any",
                reminder_count=0,
                repeat_interval_seconds=300,
                created_at=START,
                updated_at=START,
            )
        )
        for index, interval in enumerate((300, None)):
            connection.execute(
                occurrences.insert().values(
                    id=f"roc_{index}",
                    reminder_id="rem_legacy",
                    occurrence_key=f"legacy-{index}",
                    scheduled_for=START,
                    triggered_at=START,
                    status="active",
                    title_snapshot="Legacy",
                    content_snapshot="",
                    content_type_snapshot="text",
                    ack_policy_snapshot="any",
                    repeat_interval_seconds_snapshot=interval,
                    version=1,
                    created_at=START,
                    updated_at=START,
                )
            )
    engine.dispose()
    migrate("upgrade", "head")
    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT require_ack_snapshot FROM reminder_occurrences ORDER BY id")
            ).scalars().all() == [1, 0]
            assert (
                connection.execute(
                    sa.text("SELECT repeat_config FROM reminders WHERE id = 'rem_legacy'")
                ).scalar_one()
                == "{}"
            )
    finally:
        engine.dispose()
