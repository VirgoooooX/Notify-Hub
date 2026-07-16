from __future__ import annotations

import asyncio
import struct
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from app.application.reminder_service import ReminderCreate
from app.application.wecom_callback_service import WeComCallbackService
from app.application.wecom_menu_service import (
    MENU_COMPLETE,
    MENU_CREATE_ARTICLE,
    MENU_CREATE_FULL,
    MENU_CREATE_TEXT,
    MENU_IGNORE_TODAY,
    MENU_LIST_ALL,
    MENU_LIST_AWAITING,
    MENU_LIST_TODAY,
    MENU_SNOOZE_10,
    MENU_SNOOZE_30,
    MENU_STOP,
    WeComMenuService,
    build_wecom_menu_payload,
)
from app.channels.base import ChannelResult, FakeChannel
from app.channels.wecom.callback import IncomingCallback
from app.domain.reminders import AckPolicy, ScheduleType
from app.infrastructure.database.base import new_id
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.models import (
    AuditLog,
    Delivery,
    DeliveryStatus,
    Notification,
    Person,
    RecipientType,
    WeComIdentity,
)
from app.infrastructure.database.reminder_models import (
    IncomingMessage,
    Reminder,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from app.workers.delivery_worker import DeliveryWorker
from app.workers.interaction_worker import InteractionWorker
from app.workers.reminder_worker import ReminderWorker
from sqlalchemy import select

from tests.test_core_api import initialize_and_login


def _png_bytes() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


async def _member(app: object, *, name: str, user_id: str) -> tuple[Person, WeComIdentity]:
    now = app.state.clock.now()  # type: ignore[attr-defined]
    person = Person(
        id=new_id("person"),
        display_name=name,
        active=True,
        is_default=False,
        created_at=now,
        updated_at=now,
    )
    identity = WeComIdentity(
        id=new_id("wid"),
        person_id=person.id,
        user_id=user_id,
        active=True,
        created_at=now,
        updated_at=now,
    )
    async with app.state.session_factory() as session, session.begin():  # type: ignore[attr-defined]
        session.add_all([person, identity])
    return person, identity


async def _interactive_occurrence(
    app: Any,
    person: Person,
    *,
    title: str = "提交月度报表",
) -> tuple[ReminderOccurrence, ReminderOccurrenceRecipient]:
    now = app.state.clock.now()
    reminder = await app.state.reminder_service.create(
        ReminderCreate(
            creator_person_id=person.id,
            title=title,
            content="请在截止时间前提交",
            schedule_type=ScheduleType.ONCE,
            timezone="UTC",
            recipient_ids=(person.id,),
            scheduled_at=now,
            require_ack=True,
            ack_policy=AckPolicy.ANY,
            repeat_interval_seconds=300,
            max_reminders=5,
        ),
        now=now,
    )
    assert await app.state.reminder_service.claim_due(worker_id="menu-test-planner", now=now) == [
        reminder.id
    ]
    await app.state.reminder_service.trigger_claimed(
        reminder.id, worker_id="menu-test-planner", now=now
    )
    async with app.state.session_factory() as session:
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder.id)
        )
        assert occurrence is not None
        recipient = await session.scalar(
            select(ReminderOccurrenceRecipient).where(
                ReminderOccurrenceRecipient.occurrence_id == occurrence.id,
                ReminderOccurrenceRecipient.person_id == person.id,
            )
        )
        assert recipient is not None
        session.expunge(occurrence)
        session.expunge(recipient)
    return occurrence, recipient


async def _point_to(app: Any, identity: WeComIdentity, occurrence_id: str | None) -> None:
    async with app.state.session_factory() as session, session.begin():
        stored = await session.get(WeComIdentity, identity.id)
        assert stored is not None
        stored.latest_interactive_occurrence_id = occurrence_id


async def _queue_delivery(
    app: Any,
    person: Person,
    *,
    title: str,
    require_ack: bool,
    occurrence_id: str | None,
    interactive: bool = False,
) -> str:
    now = app.state.clock.now()
    notification_id = new_id("notification")
    delivery_id = new_id("delivery")
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Notification(
                id=notification_id,
                event_id=None,
                reminder_id=None,
                reminder_occurrence_id=occurrence_id,
                message_type="text",
                title=title,
                content=title,
                url=None,
                image_url=None,
                media_asset_id=None,
                ack_policy="any" if require_ack else None,
                payload={"interactive_reminder": True} if interactive else {},
                priority="normal",
                require_ack=require_ack,
                created_at=now,
                expires_at=None,
            )
        )
        session.add(
            Delivery(
                id=delivery_id,
                notification_id=notification_id,
                channel="wecom",
                recipient_type=RecipientType.PERSON.value,
                recipient_id=person.id,
                status=DeliveryStatus.PENDING.value,
                attempt_count=0,
                max_attempts=1,
                next_attempt_at=now,
                claimed_by=None,
                claim_expires_at=None,
                last_error_code=None,
                last_error_message=None,
                provider_message_id=None,
                sent_at=None,
                created_at=now,
                updated_at=now,
            )
        )
    return delivery_id


def test_menu_payload_contains_three_series_and_five_quick_operations() -> None:
    payload = build_wecom_menu_payload()
    assert payload == {
        "button": [
            {
                "name": "新建提醒",
                "sub_button": [
                    {"type": "click", "name": "快速文字提醒", "key": MENU_CREATE_TEXT},
                    {"type": "click", "name": "图文提醒", "key": MENU_CREATE_ARTICLE},
                    {"type": "click", "name": "打开完整创建页", "key": MENU_CREATE_FULL},
                ],
            },
            {
                "name": "我的提醒",
                "sub_button": [
                    {"type": "click", "name": "等待我完成", "key": MENU_LIST_AWAITING},
                    {"type": "click", "name": "今天的提醒", "key": MENU_LIST_TODAY},
                    {"type": "click", "name": "全部提醒", "key": MENU_LIST_ALL},
                ],
            },
            {
                "name": "快捷操作",
                "sub_button": [
                    {"type": "click", "name": "完成本次", "key": MENU_COMPLETE},
                    {"type": "click", "name": "推迟10分钟", "key": MENU_SNOOZE_10},
                    {"type": "click", "name": "推迟30分钟", "key": MENU_SNOOZE_30},
                    {"type": "click", "name": "今日忽略", "key": MENU_IGNORE_TODAY},
                    {"type": "click", "name": "停止本次", "key": MENU_STOP},
                ],
            },
        ]
    }


@pytest.mark.asyncio
async def test_create_and_list_menu_series_issue_user_bound_mobile_links(api) -> None:
    _client, app = api
    _person, identity = await _member(app, name="Menu links", user_id="menu-links")
    service = WeComMenuService(
        app.state.reminder_service,
        app.state.mobile_identity_service,
        "https://notify.example.com",
    )

    text_result = await service.handle("menu-links", MENU_CREATE_TEXT)
    article_result = await service.handle("menu-links", MENU_CREATE_ARTICLE)
    all_result = await service.handle("menu-links", MENU_LIST_ALL)

    assert text_result.code == "create_text"
    assert "明天下午3点提醒我提交月度报表" in text_result.text
    article_url = next(
        line for line in article_result.text.splitlines() if line.startswith("https://")
    )
    all_url = next(line for line in all_result.text.splitlines() if line.startswith("https://"))
    assert urlsplit(article_url).path == "/m/reminders/new"
    assert parse_qs(urlsplit(article_url).query)["content"] == ["article"]
    assert urlsplit(all_url).path == "/m/reminders/active"
    assert parse_qs(urlsplit(all_url).query)["scope"] == ["all"]
    entry = parse_qs(urlsplit(all_url).query)["entry"][0]
    member = await app.state.mobile_identity_service.resolve(entry)
    assert member.identity_id == identity.id


@pytest.mark.asyncio
async def test_admin_can_preview_menu_payload_without_external_side_effect(api) -> None:
    client, _app = api
    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "admin", "password": "safe-password-123"},
    )
    assert initialized.status_code == 201
    token = initialized.json()["data"]["access_token"]

    response = await client.get(
        "/api/v1/admin/wecom/menu/payload",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["applied"] is False
    assert response.json()["data"]["payload"] == build_wecom_menu_payload()


@pytest.mark.asyncio
async def test_admin_can_publish_menu_to_wecom(api) -> None:
    client, app = api
    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "menu-admin", "password": "safe-password-123"},
    )
    token = initialized.json()["data"]["access_token"]
    published: list[dict[str, object]] = []

    class MenuClient:
        async def create_menu(self, payload: dict[str, object]) -> ChannelResult:
            published.append(payload)
            return ChannelResult(True)

    app.state.settings.wecom_agent_id = 1
    app.state.wecom_client = MenuClient()
    response = await client.post(
        "/api/v1/admin/wecom/menu/publish",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "payload": build_wecom_menu_payload(),
        "applied": True,
    }
    assert published == [build_wecom_menu_payload()]
    async with app.state.session_factory() as session:
        actions = list(
            await session.scalars(
                select(AuditLog.action)
                .where(AuditLog.resource_type == "wecom_menu")
                .order_by(AuditLog.created_at)
            )
        )
    assert actions == ["wecom.menu.publish.requested", "wecom.menu.publish"]


@pytest.mark.asyncio
async def test_menu_without_latest_interactive_pointer_returns_not_found(api) -> None:
    _client, app = api
    await _member(app, name="Vigoss", user_id="vigoss")

    result = await app.state.wecom_menu_service.handle("vigoss", MENU_COMPLETE)

    assert result.code == "not_found"
    assert result.text == "当前没有可操作的交互式提醒。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_key", "expected_code", "expected_delta"),
    [
        (MENU_SNOOZE_10, "snooze_10", timedelta(minutes=10)),
        (MENU_SNOOZE_30, "snooze_30", timedelta(minutes=30)),
    ],
)
async def test_menu_snoozes_latest_occurrence_and_names_task(
    api, event_key: str, expected_code: str, expected_delta: timedelta
) -> None:
    _client, app = api
    person, identity = await _member(app, name="Snoozer", user_id="snoozer")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)
    before = app.state.clock.now()

    result = await app.state.wecom_menu_service.handle("snoozer", event_key)
    after = app.state.clock.now()

    assert result.code == expected_code
    assert occurrence.title_snapshot in result.text
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        assert stored.next_notify_at is not None
        actual = stored.next_notify_at.replace(tzinfo=UTC)
        assert before + expected_delta <= actual <= after + expected_delta


@pytest.mark.asyncio
async def test_menu_completes_latest_occurrence_and_names_task(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Completer", user_id="completer")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)

    result = await app.state.wecom_menu_service.handle("completer", MENU_COMPLETE)

    assert result.code == "completed"
    assert result.text == "✅ 已完成：提交月度报表\n\n本次持续提醒已停止。"
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        assert stored.status == "acknowledged"


@pytest.mark.asyncio
async def test_web_broadcast_tracks_all_members_and_announces_completion(api) -> None:
    client, app = api
    first, first_identity = await _member(app, name="First", user_id="broadcast-first")
    second, second_identity = await _member(app, name="Second", user_id="broadcast-second")
    scheduled = app.state.clock.now() + timedelta(seconds=1)
    token = await initialize_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/admin/reminders",
        headers=headers,
        json={
            "title": "全员提交安全确认",
            "content": "请完成安全检查",
            "schedule": {
                "type": "once",
                "at": scheduled.isoformat(),
                "timezone": "Asia/Shanghai",
            },
            "recipients": [],
            "broadcast": True,
            "notify_on_all_completed": True,
            "require_ack": True,
            "ack_policy": "all",
            "repeat": {"interval_seconds": 300, "max_attempts": 12},
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["data"]["id"]
    assert created.json()["data"]["broadcast"] is True
    assert created.json()["data"]["notify_on_all_completed"] is True

    reminder_worker = ReminderWorker(app.state.reminder_service, "broadcast-test")
    assert await reminder_worker.run_once(now=scheduled) == 1

    async with app.state.session_factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder_id)
        )
        recipients = list(
            await session.scalars(
                select(ReminderOccurrenceRecipient).where(
                    ReminderOccurrenceRecipient.occurrence_id == occurrence.id
                )
            )
        )
        notification = await session.scalar(
            select(Notification).where(
                Notification.reminder_occurrence_id == occurrence.id,
                Notification.payload["broadcast_reminder"].as_boolean().is_(True),
            )
        )
        delivery = await session.scalar(
            select(Delivery).where(Delivery.notification_id == notification.id)
        )
        assert reminder is not None and reminder.broadcast is True
        assert occurrence is not None and occurrence.broadcast_sent_at is not None
        assert {item.person_id for item in recipients} == {first.id, second.id}
        assert notification is not None
        assert "全员持续提醒" in notification.title
        assert "这不是一次性通知" in notification.content
        assert "请尽快点击" in notification.content
        assert delivery is not None
        assert delivery.recipient_type == RecipientType.BROADCAST.value

    channel = FakeChannel()
    delivery_worker = DeliveryWorker(
        app.state.session_factory,
        channel,
        app.state.clock,
        "broadcast-delivery-test",
    )
    assert await delivery_worker.process_one() is True
    assert len(channel.messages) == 1
    assert channel.messages[0].broadcast is True
    assert channel.messages[0].recipients == []

    async with app.state.session_factory() as session:
        stored_first = await session.get(WeComIdentity, first_identity.id)
        stored_second = await session.get(WeComIdentity, second_identity.id)
        assert stored_first is not None and stored_second is not None
        assert stored_first.latest_interactive_occurrence_id == occurrence.id
        assert stored_second.latest_interactive_occurrence_id == occurrence.id

    first_result = await app.state.wecom_menu_service.handle(first_identity.user_id, MENU_COMPLETE)
    assert first_result.code == "completed"
    assert await reminder_worker.run_once(now=scheduled + timedelta(seconds=1)) == 0

    second_result = await app.state.wecom_menu_service.handle(
        second_identity.user_id, MENU_COMPLETE
    )
    assert second_result.code == "completed"
    assert await reminder_worker.run_once(now=scheduled + timedelta(seconds=2)) == 1

    async with app.state.session_factory() as session:
        completion = await session.scalar(
            select(Notification).where(
                Notification.reminder_occurrence_id == occurrence.id,
                Notification.payload["broadcast_completion"].as_boolean().is_(True),
            )
        )
        completion_delivery = await session.scalar(
            select(Delivery).where(Delivery.notification_id == completion.id)
        )
        assert completion is not None
        assert completion.title == "✅ 所有人都已完成｜全员提交安全确认"
        assert completion_delivery is not None
        assert completion_delivery.recipient_type == RecipientType.BROADCAST.value


@pytest.mark.asyncio
async def test_all_completed_notification_requires_all_policy(api) -> None:
    client, app = api
    await _member(app, name="Member", user_id="broadcast-policy")
    token = await initialize_and_login(client)
    response = await client.post(
        "/api/v1/admin/reminders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "错误广播",
            "schedule": {
                "type": "once",
                "at": (app.state.clock.now() + timedelta(minutes=1)).isoformat(),
            },
            "broadcast": True,
            "notify_on_all_completed": True,
            "require_ack": True,
            "ack_policy": "any",
            "repeat": {"interval_seconds": 300, "max_attempts": 3},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_interactive_broadcast_without_completion_notice_allows_any_policy(api) -> None:
    client, app = api
    await _member(app, name="Member", user_id="broadcast-any-policy")
    token = await initialize_and_login(client)
    response = await client.post(
        "/api/v1/admin/reminders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "任一成员确认即可",
            "schedule": {
                "type": "once",
                "at": (app.state.clock.now() + timedelta(minutes=1)).isoformat(),
            },
            "broadcast": True,
            "require_ack": True,
            "ack_policy": "any",
            "repeat": {"interval_seconds": 300, "max_attempts": 3},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["broadcast"] is True
    assert response.json()["data"]["require_ack"] is True
    assert response.json()["data"]["ack_policy"] == "any"
    assert response.json()["data"]["notify_on_all_completed"] is False


@pytest.mark.asyncio
async def test_plain_broadcast_is_a_one_time_notification_without_interaction(api) -> None:
    client, app = api
    _person, identity = await _member(app, name="Plain", user_id="broadcast-plain")
    scheduled = app.state.clock.now() + timedelta(seconds=1)
    token = await initialize_and_login(client)
    response = await client.post(
        "/api/v1/admin/reminders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "全员普通通知",
            "content": "今天下午停机维护",
            "schedule": {"type": "once", "at": scheduled.isoformat()},
            "broadcast": True,
            "require_ack": False,
        },
    )
    assert response.status_code == 201, response.text

    reminder_worker = ReminderWorker(app.state.reminder_service, "plain-broadcast-test")
    assert await reminder_worker.run_once(now=scheduled) == 1

    async with app.state.session_factory() as session:
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(
                ReminderOccurrence.reminder_id == response.json()["data"]["id"]
            )
        )
        notification = await session.scalar(
            select(Notification).where(
                Notification.reminder_occurrence_id == occurrence.id,
                Notification.payload["broadcast_reminder"].as_boolean().is_(True),
            )
        )
        stored_identity = await session.get(WeComIdentity, identity.id)
        assert occurrence is not None and occurrence.status == "acknowledged"
        assert notification is not None and notification.require_ack is False
        assert "持续提醒" not in notification.title
        assert "请尽快点击" not in notification.content
        assert stored_identity is not None
        assert stored_identity.latest_interactive_occurrence_id is None


@pytest.mark.asyncio
async def test_concurrent_menu_actions_only_apply_one_transition(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Concurrent", user_id="concurrent")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)

    results = await asyncio.gather(
        app.state.wecom_menu_service.handle("concurrent", MENU_COMPLETE),
        app.state.wecom_menu_service.handle("concurrent", MENU_SNOOZE_10),
    )

    assert {result.code for result in results} == {"completed", "not_active"}
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        assert stored.status == "acknowledged"
        assert stored.next_notify_at is None


@pytest.mark.asyncio
async def test_admin_detail_exposes_current_menu_target_by_user_id(api) -> None:
    client, app = api
    initialized = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "detail-admin", "password": "safe-password-123"},
    )
    token = initialized.json()["data"]["access_token"]
    person, identity = await _member(app, name="Target", user_id="target-user")
    occurrence, _recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)

    response = await client.get(
        f"/api/v1/admin/reminders/{occurrence.reminder_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["interaction_mode"] == "latest_menu"
    recipient = data["occurrences"][0]["recipients"][0]
    assert recipient["latest_interactive_user_ids"] == ["target-user"]


@pytest.mark.asyncio
async def test_menu_ignores_latest_occurrence_for_today_and_names_task(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Ignore", user_id="ignore")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)

    result = await app.state.wecom_menu_service.handle("ignore", MENU_IGNORE_TODAY)

    assert result.code == "ignored_today"
    assert "今日已忽略：提交月度报表" in result.text
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        assert stored.next_notify_at is not None
        next_notify_at = stored.next_notify_at.replace(tzinfo=UTC)
        assert next_notify_at.date() == (app.state.clock.now() + timedelta(days=1)).date()
        assert next_notify_at.time() == datetime.min.time()


@pytest.mark.asyncio
async def test_menu_stops_only_latest_occurrence_and_names_task(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Stopper", user_id="stopper")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)

    result = await app.state.wecom_menu_service.handle("stopper", MENU_STOP)

    assert result.code == "stopped"
    assert "已停止本次：提交月度报表" in result.text
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        assert stored.status == "cancelled"


@pytest.mark.asyncio
async def test_inactive_latest_pointer_does_not_fall_back_to_older_active_occurrence(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="No fallback", user_id="no-fallback")
    _older, older_recipient = await _interactive_occurrence(app, person, title="更早的活跃提醒")
    latest, latest_recipient = await _interactive_occurrence(
        app, person, title="最近但已结束的提醒"
    )
    async with app.state.session_factory() as session, session.begin():
        stored = await session.get(ReminderOccurrenceRecipient, latest_recipient.id)
        assert stored is not None
        stored.status = "cancelled"
    await _point_to(app, identity, latest.id)

    result = await app.state.wecom_menu_service.handle("no-fallback", MENU_COMPLETE)

    assert result.code == "not_active"
    assert "最近但已结束的提醒" in result.text
    assert "不会自动回退" in result.text
    async with app.state.session_factory() as session:
        older_stored = await session.get(ReminderOccurrenceRecipient, older_recipient.id)
        assert older_stored is not None
        assert older_stored.status == "pending"
        pointer = await session.get(WeComIdentity, identity.id)
        assert pointer is not None
        assert pointer.latest_interactive_occurrence_id == latest.id


@pytest.mark.asyncio
async def test_persisted_menu_callback_is_dispatched_by_interaction_worker(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Menu user", user_id="menu-user")
    occurrence, _recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)
    callback_service = WeComCallbackService(app.state.session_factory)
    await callback_service.accept(
        IncomingCallback(
            sender_external_id="menu-user",
            provider_message_id=None,
            message_type="event",
            text=None,
            media_refs={},
            event="click",
            event_key=MENU_COMPLETE,
            action_token=None,
            response_code=None,
            received_at=app.state.clock.now(),
            dedupe_key="a" * 64,
        )
    )
    replies: list[str] = []

    async def emit_reply(_user_id: str, _message_id: str, text: str) -> None:
        replies.append(text)

    worker = InteractionWorker(
        app.state.session_factory,
        app.state.reminder_service,
        app.state.conversation_service,
        emit_reply=emit_reply,
        menu_service=WeComMenuService(app.state.reminder_service),
        clock=app.state.clock,
    )

    assert await worker.run_once() == 1
    assert replies == ["✅ 已完成：提交月度报表\n\n本次持续提醒已停止。"]


@pytest.mark.asyncio
async def test_retried_menu_message_does_not_apply_snooze_twice(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Retry menu", user_id="retry-menu")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)
    receipt = await app.state.wecom_callback_service.accept(
        IncomingCallback(
            sender_external_id="retry-menu",
            provider_message_id=None,
            message_type="event",
            text=None,
            media_refs={},
            event="click",
            event_key=MENU_SNOOZE_10,
            action_token=None,
            response_code=None,
            received_at=app.state.clock.now(),
            dedupe_key="c" * 64,
        )
    )
    replies: list[str] = []

    async def emit_reply(_user_id: str, _message_id: str, text: str) -> None:
        replies.append(text)

    worker = InteractionWorker(
        app.state.session_factory,
        app.state.reminder_service,
        app.state.conversation_service,
        emit_reply=emit_reply,
        menu_service=app.state.wecom_menu_service,
        clock=app.state.clock,
    )
    assert await worker.run_once() == 1
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        first_next_notify_at = stored.next_notify_at

    # Simulate a crash after the business transaction but before the message ack persisted.
    async with app.state.session_factory() as session, session.begin():
        message = await session.get(IncomingMessage, receipt.incoming_message_id)
        assert message is not None
        message.processing_status = "pending"
        message.processed_at = None

    assert await worker.run_once() == 1
    async with app.state.session_factory() as session:
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert stored is not None
        assert stored.next_notify_at == first_next_notify_at
    assert len(replies) == 2
    assert replies[0] == replies[1]
    assert "提交月度报表" in replies[1]


@pytest.mark.asyncio
async def test_menu_reply_failure_is_retried_with_original_result(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Reply retry", user_id="reply-retry")
    occurrence, recipient = await _interactive_occurrence(app, person)
    await _point_to(app, identity, occurrence.id)
    receipt = await app.state.wecom_callback_service.accept(
        IncomingCallback(
            sender_external_id="reply-retry",
            provider_message_id=None,
            message_type="event",
            text=None,
            media_refs={},
            event="click",
            event_key=MENU_COMPLETE,
            action_token=None,
            response_code=None,
            received_at=app.state.clock.now(),
            dedupe_key="d" * 64,
        )
    )
    attempts = 0
    replies: list[str] = []

    async def emit_reply(_user_id: str, _message_id: str, text: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary reply failure")
        replies.append(text)

    worker = InteractionWorker(
        app.state.session_factory,
        app.state.reminder_service,
        app.state.conversation_service,
        emit_reply=emit_reply,
        menu_service=app.state.wecom_menu_service,
        clock=app.state.clock,
    )

    assert await worker.run_once() == 1
    async with app.state.session_factory() as session:
        pending = await session.get(IncomingMessage, receipt.incoming_message_id)
        stored = await session.get(ReminderOccurrenceRecipient, recipient.id)
        assert pending is not None and pending.processing_status == "pending"
        assert stored is not None and stored.status == "acknowledged"
    assert await worker.run_once() == 1
    assert replies == ["✅ 已完成：提交月度报表\n\n本次持续提醒已停止。"]
    async with app.state.session_factory() as session:
        processed = await session.get(IncomingMessage, receipt.incoming_message_id)
        assert processed is not None and processed.processing_status == "processed"


@pytest.mark.asyncio
async def test_card_action_event_is_not_misrouted_as_a_menu_click(api) -> None:
    _client, app = api
    await _member(app, name="Action user", user_id="action-user")
    await app.state.wecom_callback_service.accept(
        IncomingCallback(
            sender_external_id="action-user",
            provider_message_id=None,
            message_type="event",
            text=None,
            media_refs={},
            event="click",
            event_key="reminder_complete:unknown-token",
            action_token="unknown-token",
            response_code=None,
            received_at=app.state.clock.now(),
            dedupe_key="b" * 64,
        )
    )
    replies: list[str] = []

    async def emit_reply(_user_id: str, _message_id: str, text: str) -> None:
        replies.append(text)

    worker = InteractionWorker(
        app.state.session_factory,
        app.state.reminder_service,
        app.state.conversation_service,
        emit_reply=emit_reply,
        menu_service=app.state.wecom_menu_service,
        clock=app.state.clock,
    )

    assert await worker.run_once() == 2
    assert replies == []


@pytest.mark.asyncio
async def test_delivery_updates_pointer_only_after_successful_interactive_send(api) -> None:
    _client, app = api
    person, identity = await _member(app, name="Delivery", user_id="delivery-user")
    occurrence, _recipient = await _interactive_occurrence(app, person)

    failed_id = await _queue_delivery(
        app,
        person,
        title="失败的交互提醒",
        require_ack=True,
        occurrence_id=occurrence.id,
        interactive=True,
    )
    failed_worker = DeliveryWorker(
        app.state.session_factory,
        FakeChannel(ChannelResult(False, False, "SEND_FAILED")),
        app.state.clock,
        "failed-pointer-worker",
    )
    assert await failed_worker.process_one() is True
    async with app.state.session_factory() as session:
        failed = await session.get(Delivery, failed_id)
        stored_identity = await session.get(WeComIdentity, identity.id)
        assert failed is not None and failed.status == DeliveryStatus.DEAD.value
        assert stored_identity is not None
        assert stored_identity.latest_interactive_occurrence_id is None

    ordinary_id = await _queue_delivery(
        app,
        person,
        title="普通通知",
        require_ack=False,
        occurrence_id=occurrence.id,
    )
    ordinary_worker = DeliveryWorker(
        app.state.session_factory, FakeChannel(), app.state.clock, "ordinary-pointer-worker"
    )
    assert await ordinary_worker.process_one() is True
    async with app.state.session_factory() as session:
        ordinary = await session.get(Delivery, ordinary_id)
        stored_identity = await session.get(WeComIdentity, identity.id)
        assert ordinary is not None and ordinary.status == DeliveryStatus.SUCCEEDED.value
        assert stored_identity is not None
        assert stored_identity.latest_interactive_occurrence_id is None

    confirmation_id = await _queue_delivery(
        app,
        person,
        title="操作确认消息",
        require_ack=False,
        occurrence_id=None,
    )
    confirmation_worker = DeliveryWorker(
        app.state.session_factory,
        FakeChannel(),
        app.state.clock,
        "confirmation-pointer-worker",
    )
    assert await confirmation_worker.process_one() is True
    async with app.state.session_factory() as session:
        confirmation = await session.get(Delivery, confirmation_id)
        stored_identity = await session.get(WeComIdentity, identity.id)
        assert confirmation is not None
        assert confirmation.status == DeliveryStatus.SUCCEEDED.value
        assert stored_identity is not None
        assert stored_identity.latest_interactive_occurrence_id is None

    interactive_id = await _queue_delivery(
        app,
        person,
        title="成功的交互提醒",
        require_ack=True,
        occurrence_id=occurrence.id,
        interactive=True,
    )
    interactive_worker = DeliveryWorker(
        app.state.session_factory,
        FakeChannel(),
        app.state.clock,
        "interactive-pointer-worker",
    )
    assert await interactive_worker.process_one() is True
    async with app.state.session_factory() as session:
        interactive = await session.get(Delivery, interactive_id)
        stored_identity = await session.get(WeComIdentity, identity.id)
        assert interactive is not None
        assert interactive.status == DeliveryStatus.SUCCEEDED.value
        assert stored_identity is not None
        assert stored_identity.latest_interactive_occurrence_id == occurrence.id


@pytest.mark.asyncio
async def test_mobile_api_enforces_member_scope_and_supports_image_creation(api) -> None:
    client, app = api
    owner, owner_identity = await _member(app, name="Owner", user_id="owner")
    _other, other_identity = await _member(app, name="Other", user_id="other")
    owner_token = app.state.mobile_identity_service.issue(owner_identity.id)
    owner_headers = {"X-Mobile-Token": owner_token}

    upload = await client.post(
        "/api/v1/mobile/media",
        headers=owner_headers,
        files={"file": ("note.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    media_id = upload.json()["data"]["id"]

    created = await client.post(
        "/api/v1/mobile/reminders",
        headers=owner_headers,
        json={
            "title": "带药",
            "content": "出门前检查",
            "content_type": "article",
            "media_asset_id": media_id,
            "schedule": {
                "type": "once",
                "at": (app.state.clock.now() + timedelta(hours=1)).isoformat(),
                "timezone": "Asia/Shanghai",
            },
            "require_ack": True,
            "repeat": {"interval_seconds": 300, "max_attempts": 3},
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["data"]["id"]
    assert created.json()["data"]["media_asset_id"] == media_id

    listed = await client.get("/api/v1/mobile/reminders", headers=owner_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]["items"]] == [reminder_id]

    async with app.state.session_factory() as session, session.begin():
        stored_reminder = await session.get(Reminder, reminder_id)
        assert stored_reminder is not None
        stored_reminder.status = "completed"

    active_after_completion = await client.get(
        "/api/v1/mobile/reminders?scope=active", headers=owner_headers
    )
    all_after_completion = await client.get(
        "/api/v1/mobile/reminders?scope=all", headers=owner_headers
    )
    assert active_after_completion.status_code == 200
    assert active_after_completion.json()["data"]["items"] == []
    assert all_after_completion.status_code == 200
    assert [item["id"] for item in all_after_completion.json()["data"]["items"]] == [reminder_id]

    hidden = await client.get(
        f"/api/v1/mobile/reminders/{reminder_id}",
        headers={"X-Mobile-Token": app.state.mobile_identity_service.issue(other_identity.id)},
    )
    assert hidden.status_code == 404

    async with app.state.session_factory() as session:
        asset = await session.get(MediaAsset, media_id)
        assert asset is not None
        assert asset.created_by == owner.id


@pytest.mark.asyncio
async def test_mobile_token_is_revoked_when_identity_is_disabled(api) -> None:
    client, app = api
    _person, identity = await _member(app, name="Disabled", user_id="disabled")
    token = app.state.mobile_identity_service.issue(identity.id)
    async with app.state.session_factory() as session, session.begin():
        stored = await session.get(WeComIdentity, identity.id)
        assert stored is not None
        stored.active = False

    response = await client.get("/api/v1/mobile/session", headers={"X-Mobile-Token": token})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_mobile_token"
