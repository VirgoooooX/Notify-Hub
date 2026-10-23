"""Durable, user-triggered login maintenance; never log out on a timer."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import structlog
from app.application.event_service import EventService
from app.application.media_service import MediaService
from app.application.time_utils import ensure_utc
from app.channels.browser_publisher.client import BrowserPublisherClient
from app.domain.clock import Clock
from app.infrastructure.database.media_models import MediaAsset
from app.infrastructure.database.models import Notification
from app.infrastructure.database.reminder_models import (
    Reminder,
    ReminderLoginAction,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
)
from app.media.validation import MediaKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class PublisherLoginService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        publisher: BrowserPublisherClient,
        events: EventService,
        media: MediaService,
        clock: Clock,
    ) -> None:
        self.sessions, self.publisher, self.events, self.media, self.clock = (
            sessions,
            publisher,
            events,
            media,
            clock,
        )
        self._lock = asyncio.Lock()

    async def run_once(self) -> None:
        async with self._lock:
            now = self.clock.now()
            async with self.sessions() as session:
                ids = list(
                    await session.scalars(
                        select(ReminderLoginAction.id)
                        .where(
                            ReminderLoginAction.status.in_(
                                (
                                    "pending",
                                    "starting",
                                    "qr_ready",
                                    "waiting_scan",
                                    "failed_pending",
                                )
                            ),
                            ReminderLoginAction.next_check_at <= now,
                        )
                        .limit(20)
                    )
                )
            for action_id in ids:
                try:
                    await self._process(action_id)
                except Exception as exc:
                    structlog.get_logger().warning(
                        "publisher_login_action_failed",
                        action_id=action_id,
                        error_type=type(exc).__name__,
                    )
                    # A failed/unknown mutating HTTP request must NEVER be
                    # automatically repeated. The next pass only checks recovery.
                    async with self.sessions() as session, session.begin():
                        action = await session.get(ReminderLoginAction, action_id)
                        if action is not None:
                            action.next_check_at = self.clock.now() + timedelta(seconds=15)
                            elapsed = self.clock.now() - ensure_utc(action.updated_at)
                            if (
                                action.status == "pending"
                                or (action.status == "starting" and elapsed >= timedelta(minutes=2))
                                or (
                                    action.status == "waiting_scan"
                                    and elapsed >= timedelta(minutes=30)
                                )
                            ):
                                action.transition("failed_pending", now=self.clock.now())

    async def _process(self, action_id: str) -> None:
        now = self.clock.now()
        async with self.sessions() as session, session.begin():
            action = await session.get(ReminderLoginAction, action_id)
            if action is None:
                return
            recipient = await session.get(
                ReminderOccurrenceRecipient, action.occurrence_recipient_id
            )
            occurrence = (
                await session.get(ReminderOccurrence, recipient.occurrence_id)
                if recipient
                else None
            )
            reminder = await session.get(Reminder, occurrence.reminder_id) if occurrence else None
            if (
                recipient is None
                or occurrence is None
                or reminder is None
                or recipient.status != "pending"
                or occurrence.status != "active"
                or reminder.status != "active"
                or occurrence.completion_action_snapshot != "publisher_wechat_login"
            ):
                action.transition("cancelled", now=now)
                return
            generation, phase = action.generation, action.status
            requested_at = ensure_utc(action.requested_at)
            media_id = action.media_asset_id
            if phase == "pending":
                # Commit before any request that could clear the remote login.
                action.transition("starting", now=now)
                action.updated_at = now
                action.next_check_at = now + timedelta(seconds=15)

        if phase in {"pending", "starting", "waiting_scan"}:
            state = await self.publisher.login_state()
            authenticated_at = state.get("last_auth_at")
            parsed_auth = (
                datetime.fromisoformat(authenticated_at)
                if isinstance(authenticated_at, str)
                else None
            )
            # Browser Publisher's legacy SQLite DateTime serializes UTC without
            # an offset; normalize only at this explicit integration boundary.
            if parsed_auth is not None and parsed_auth.tzinfo is None:
                parsed_auth = parsed_auth.replace(tzinfo=UTC)
            authenticated = (
                state.get("session_state") == "ready"
                and parsed_auth is not None
                and ensure_utc(parsed_auth) > requested_at
            )
            if authenticated:
                await self._complete(action_id, generation, recipient.id, occurrence.id)
                return
            if phase == "pending":
                # Publisher operations are serialized with publication by its lock.
                image = await self.publisher.request_login(
                    refresh_only=state.get("session_state") == "auth_required"
                )
                async with self.sessions() as session:
                    asset = await self.media.create(
                        session,
                        image,
                        MediaKind.IMAGE,
                        source="publisher-login",
                        created_by=recipient.person_id,
                    )
                    media_id = asset.id
                async with self.sessions() as session, session.begin():
                    current = await session.get(ReminderLoginAction, action_id)
                    if current is None or current.generation != generation:
                        return
                    current.media_asset_id = media_id
                    current.transition("qr_ready", now=now)
                phase = "qr_ready"
            elif phase == "starting":
                # Crash/timeout between logout and receipt: do not repeat logout.
                # Give the remote request time to settle before reporting failure.
                async with self.sessions() as session, session.begin():
                    current = await session.get(ReminderLoginAction, action_id)
                    if current and current.generation == generation:
                        if now - ensure_utc(current.updated_at) >= timedelta(minutes=2):
                            current.transition("failed_pending", now=now)
                        current.next_check_at = now + timedelta(seconds=15)
                return
            else:
                async with self.sessions() as session, session.begin():
                    current = await session.get(ReminderLoginAction, action_id)
                    if current and current.generation == generation:
                        if now - ensure_utc(current.updated_at) >= timedelta(minutes=30):
                            current.transition("failed_pending", now=now)
                        current.next_check_at = now + timedelta(seconds=15)
                return

        if phase in {"qr_ready", "failed_pending"}:
            async with self.sessions() as session:
                active_recipient = await session.get(ReminderOccurrenceRecipient, recipient.id)
                if active_recipient is None or active_recipient.status != "pending":
                    return
            failed = phase == "failed_pending"
            outcome = "failed" if failed else "qr"
            accepted = await self.events.accept_internal_event(
                source_type="system",
                source_id="publisher-login",
                profile_id=occurrence.profile_id_snapshot,
                event_type="publisher.login.failed" if failed else "publisher.login.qr",
                event_key=f"publisher-login:{action_id}:{generation}:{outcome}",
                title=f"{'登录未完成' if failed else '扫码重新登录'}：{occurrence.title_snapshot}",
                content=(
                    "本次登录未完成或二维码获取失败。准备好扫码后，可在这条维护提醒仍为最近操作目标时，再点【完成本次】获取新码。"
                    if failed
                    else "请使用公众号管理员微信扫码并确认。"
                    "二维码过期后，可再次点击【完成本次】获取新码。"
                ),
                recipients=[recipient.person_id],
                reminder_occurrence_id=occurrence.id,
                message_type="text" if failed else "image",
                media_asset_id=None if failed else media_id,
            )
            async with self.sessions() as session, session.begin():
                if not failed and media_id is not None:
                    notification = await session.scalar(
                        select(Notification).where(Notification.event_id == accepted.event_id)
                    )
                    qr_asset = await session.get(MediaAsset, media_id)
                    if notification is not None and qr_asset is not None:
                        # Delivery freshness bound, not a claim about WeChat's
                        # QR lifetime. Never deliver an old code after long retries.
                        notification.expires_at = qr_asset.created_at + timedelta(minutes=2)
                current = await session.get(ReminderLoginAction, action_id)
                if current and current.generation == generation:
                    current.transition("failed" if failed else "waiting_scan", now=now)
                    current.updated_at = now
                    current.next_check_at = now + timedelta(seconds=15)

    async def _complete(
        self, action_id: str, generation: int, recipient_id: str, occurrence_id: str
    ) -> None:
        async with self.sessions() as session, session.begin():
            action = await session.get(ReminderLoginAction, action_id)
            recipient = await session.get(ReminderOccurrenceRecipient, recipient_id)
            occurrence = await session.get(ReminderOccurrence, occurrence_id)
            if (
                action is None
                or action.generation != generation
                or recipient is None
                or occurrence is None
                or recipient.status != "pending"
                or occurrence.status != "active"
            ):
                return
            now = self.clock.now()
            action.transition("completed", now=now)
            recipient.status, recipient.acknowledged_at = "acknowledged", now
            recipient.acknowledged_by, recipient.next_notify_at = recipient.person_id, None
            recipient.claimed_by, recipient.claim_expires_at = None, None
            occurrence.status, occurrence.completed_at = "acknowledged", now
            occurrence.completed_by, occurrence.updated_at = recipient.person_id, now
            reminder = await session.get(Reminder, occurrence.reminder_id)
            if reminder is not None and reminder.next_run_at is None:
                reminder.status = "completed"
            # The repeating Reminder definition and next cycle remain active.

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                structlog.get_logger().warning(
                    "publisher_login_worker_failed", error_type=type(exc).__name__
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2.0)
