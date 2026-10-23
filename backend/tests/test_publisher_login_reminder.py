from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.application.publisher_login_service import PublisherLoginService
from app.application.reminder_service import ReminderCreate, ReminderUpdate
from app.application.wecom_menu_service import MENU_COMPLETE
from app.domain.reminders import ScheduleType
from app.infrastructure.database.models import Event, Notification
from app.infrastructure.database.reminder_models import (
    IncomingMessage,
    ReminderLoginAction,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from app.workers.interaction_worker import InteractionWorker
from app.workers.reminder_worker import ReminderWorker
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.test_wecom_mobile import _member, _png_bytes, _point_to


class Clock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


async def setup(app: Any) -> tuple[Any, Any, Clock, Any]:
    person, identity = await _member(app, name="Publisher owner", user_id="publisher-owner")
    clock = Clock(app.state.clock.now())
    app.state.reminder_service._clock = clock
    reminder = await app.state.reminder_service.create(
        ReminderCreate(
            creator_person_id=person.id,
            title="公众号每4天登录维护",
            content="请按需维护登录",
            schedule_type=ScheduleType.INTERVAL,
            timezone="Asia/Shanghai",
            recipient_ids=(person.id,),
            start_at=clock.now(),
            interval_seconds=4 * 86400,
            completion_action="publisher_wechat_login",
        ),
        now=clock.now(),
    )
    assert await ReminderWorker(app.state.reminder_service).run_once(now=clock.now()) == 1
    async with app.state.session_factory() as session:
        occurrence = await session.scalar(
            select(ReminderOccurrence).where(ReminderOccurrence.reminder_id == reminder.id)
        )
    await _point_to(app, identity, occurrence.id)
    fake = AsyncMock()
    fake.login_state.return_value = {
        "session_state": "ready",
        "last_auth_at": (clock.now() - timedelta(days=4)).isoformat(),
    }
    fake.request_login.return_value = _png_bytes()
    service = PublisherLoginService(
        app.state.session_factory, fake, app.state.event_service, app.state.media_service, clock
    )
    return service, fake, clock, occurrence


@pytest.mark.asyncio
async def test_no_click_preserves_login_and_reminder_explains_the_action(
    api: tuple[Any, Any],
) -> None:
    _, app = api
    service, publisher, clock, occurrence = await setup(app)
    await service.run_once()
    clock.current += timedelta(days=1)
    assert await ReminderWorker(app.state.reminder_service).run_once(now=clock.now()) == 0
    publisher.login_state.assert_not_awaited()
    publisher.request_login.assert_not_awaited()
    async with app.state.session_factory() as session:
        notification = await session.scalar(
            select(Notification).where(Notification.reminder_occurrence_id == occurrence.id)
        )
        assert "【快捷操作】→【完成本次】" in notification.content
        assert "暂不操作时保留现有登录态" in notification.content
        assert await session.scalar(select(func.count(ReminderLoginAction.id))) == 0


@pytest.mark.asyncio
async def test_click_is_silent_sends_one_qr_and_only_scan_completes(api: tuple[Any, Any]) -> None:
    _, app = api
    service, publisher, clock, occurrence = await setup(app)
    for _ in range(2):
        reply = await app.state.wecom_menu_service.handle("publisher-owner", MENU_COMPLETE)
        assert reply.code == "login_requested" and reply.text == ""
    await service.run_once()
    publisher.request_login.assert_awaited_once_with(refresh_only=False)
    async with app.state.session_factory() as session:
        action = await session.scalar(select(ReminderLoginAction))
        recipient = await session.get(ReminderOccurrenceRecipient, action.occurrence_recipient_id)
        assert action.status == "waiting_scan" and action.generation == 1
        assert recipient.status == "pending"
        assert (await session.get(ReminderOccurrence, occurrence.id)).status == "active"
        messages = list(
            await session.scalars(select(Notification).where(Notification.message_type == "image"))
        )
        assert len(messages) == 1 and messages[0].media_asset_id == action.media_asset_id
        assert "正在获取" not in messages[0].content
    publisher.login_state.return_value = {
        "session_state": "ready",
        "last_auth_at": (clock.now() + timedelta(seconds=1)).replace(tzinfo=None).isoformat(),
    }
    clock.current += timedelta(seconds=20)
    await service.run_once()
    async with app.state.session_factory() as session:
        assert (await session.scalar(select(ReminderLoginAction))).status == "completed"
        assert (await session.get(ReminderOccurrence, occurrence.id)).status == "acknowledged"
    assert (await app.state.reminder_service.get(occurrence.reminder_id)).next_run_at is not None


@pytest.mark.asyncio
async def test_expired_qr_refreshes_only_after_another_click(api: tuple[Any, Any]) -> None:
    _, app = api
    service, publisher, clock, _ = await setup(app)
    await app.state.wecom_menu_service.handle("publisher-owner", MENU_COMPLETE)
    await service.run_once()
    publisher.login_state.return_value = {"session_state": "auth_required", "last_auth_at": None}
    clock.current += timedelta(minutes=5)
    await service.run_once()
    assert publisher.request_login.await_count == 1

    await app.state.wecom_menu_service.handle("publisher-owner", MENU_COMPLETE)
    await service.run_once()
    assert publisher.request_login.await_count == 2
    assert publisher.request_login.call_args.kwargs == {"refresh_only": True}
    async with app.state.session_factory() as session:
        assert (await session.scalar(select(ReminderLoginAction))).generation == 2
        assert (
            await session.scalar(
                select(func.count(Event.id)).where(Event.event_type == "publisher.login.qr")
            )
            == 2
        )


def test_login_action_rejects_illegal_transitions() -> None:
    from app.domain.reminders import InvalidReminderTransition, transition_login_action

    with pytest.raises(InvalidReminderTransition):
        transition_login_action("completed", "pending")
    with pytest.raises(InvalidReminderTransition):
        transition_login_action("pending", "waiting_scan")


@pytest.mark.asyncio
async def test_login_action_database_constraints(api: tuple[Any, Any]) -> None:
    _, app = api
    _, _, clock, _ = await setup(app)
    await app.state.wecom_menu_service.handle("publisher-owner", MENU_COMPLETE)
    async with app.state.session_factory() as session:
        action = await session.scalar(select(ReminderLoginAction))
    for status, generation in (("invalid", 1), ("pending", 0), ("pending", 1)):
        async with app.state.session_factory() as session:
            with pytest.raises(IntegrityError):
                async with session.begin():
                    session.add(
                        ReminderLoginAction(
                            id=f"duplicate-{status}-{generation}",
                            occurrence_recipient_id=action.occurrence_recipient_id,
                            status=status,
                            generation=generation,
                            requested_at=clock.now(),
                            updated_at=clock.now(),
                            next_check_at=clock.now(),
                        )
                    )
                    await session.flush()


@pytest.mark.asyncio
async def test_publisher_login_client_checks_protocol_and_auth_without_leaking_secrets(
    monkeypatch,
) -> None:
    import httpx
    from app.channels.browser_publisher.client import BrowserPublisherClient, BrowserPublisherError

    calls = []
    supported = True

    def response(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer private-test-token"
        if request.url.path == "/v1/platforms":
            return httpx.Response(
                200,
                json={
                    "caller_login_notifications_supported": supported,
                    "platforms": [{"platform": "wechat_mp", "session_state": "ready"}],
                },
            )
        if request.method == "POST":
            assert request.url.params["notify"] == "false"
            return httpx.Response(200, json={"status": "auth_required"})
        return httpx.Response(200, content=_png_bytes(), headers={"Content-Type": "image/png"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    client = BrowserPublisherClient("http://publisher", "private-test-token")
    assert (await client.login_state())["session_state"] == "ready"
    assert await client.request_login(refresh_only=False) == _png_bytes()
    assert [call.url.path for call in calls] == [
        "/v1/platforms",
        "/v1/platforms/wechat_mp/reauth",
        "/v1/platforms/wechat_mp/qr.png",
    ]
    supported = False
    with pytest.raises(BrowserPublisherError) as error:
        await client.login_state()
    assert "private-test-token" not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_and_restart_never_repeat_logout(api: tuple[Any, Any]) -> None:
    _, app = api
    service, publisher, clock, occurrence = await setup(app)
    publisher.request_login.side_effect = TimeoutError("do not leak secret")
    await app.state.wecom_menu_service.handle("publisher-owner", MENU_COMPLETE)
    await service.run_once()
    restarted = PublisherLoginService(
        app.state.session_factory,
        publisher,
        app.state.event_service,
        app.state.media_service,
        clock,
    )
    clock.current += timedelta(minutes=3)
    await restarted.run_once()
    clock.current += timedelta(seconds=20)
    await restarted.run_once()
    assert publisher.request_login.await_count == 1
    async with app.state.session_factory() as session:
        assert (await session.scalar(select(ReminderLoginAction))).status == "failed"
        failure = await session.scalar(
            select(Event).where(Event.event_type == "publisher.login.failed")
        )
        assert failure is not None and "secret" not in failure.content
        assert (await session.get(ReminderOccurrence, occurrence.id)).status == "active"


@pytest.mark.asyncio
async def test_stop_and_permission_checks_prevent_remote_action(api: tuple[Any, Any]) -> None:
    _, app = api
    service, publisher, _, occurrence = await setup(app)
    denied = await app.state.wecom_menu_service.handle("unrecognized", MENU_COMPLETE)
    assert denied.code in {"forbidden", "not_found"}
    await app.state.wecom_menu_service.handle("publisher-owner", MENU_COMPLETE)
    await app.state.reminder_service.operate_latest_interactive(
        sender_wecom_userid="publisher-owner", operation="stop"
    )
    await service.run_once()
    publisher.request_login.assert_not_awaited()
    async with app.state.session_factory() as session:
        assert (await session.scalar(select(ReminderLoginAction))).status == "cancelled"
        assert (await session.get(ReminderOccurrence, occurrence.id)).status == "cancelled"


@pytest.mark.asyncio
async def test_admin_can_upgrade_existing_static_reminder_without_changing_schedule(
    api: tuple[Any, Any],
) -> None:
    _, app = api
    _, _, _, occurrence = await setup(app)
    before = await app.state.reminder_service.get(occurrence.reminder_id)
    updated = await app.state.reminder_service.update(
        before.id, ReminderUpdate(completion_action="publisher_wechat_login")
    )
    assert updated.schedule_config == before.schedule_config == {"seconds": 4 * 86400}
    assert updated.next_run_at == before.next_run_at
    assert updated.require_ack and updated.max_reminders == 1


@pytest.mark.asyncio
async def test_replayed_incoming_click_does_not_generate_a_second_qr(api: tuple[Any, Any]) -> None:
    _, app = api
    service, publisher, clock, _ = await setup(app)
    async with app.state.session_factory() as session, session.begin():
        session.add(
            IncomingMessage(
                id="click-login",
                profile_id="profile_notify_hub",
                channel="wecom",
                sender_external_id="publisher-owner",
                provider_message_id="click-login",
                dedupe_key="click-login",
                message_type="event",
                media_refs={},
                event_payload={"event": "click", "event_key": MENU_COMPLETE, "has_action": False},
                received_at=clock.now(),
                processing_status="pending",
            )
        )
    emit_reply = AsyncMock()
    interactions = InteractionWorker(
        app.state.session_factory,
        app.state.reminder_service,
        app.state.conversation_service,
        emit_reply_profile=emit_reply,
        menu_service=app.state.wecom_menu_service,
        clock=clock,
    )
    assert await interactions.run_once(now=clock.now()) == 1
    emit_reply.assert_not_awaited()
    await service.run_once()
    await app.state.wecom_menu_service.handle(
        "publisher-owner", MENU_COMPLETE, incoming_message_id="click-login"
    )
    clock.current += timedelta(seconds=20)
    publisher.login_state.return_value = {"session_state": "auth_required"}
    await service.run_once()
    assert publisher.request_login.await_count == 1
