from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from app.application.reminder_service import ReminderCreate, ReminderService
from app.domain.reminders import AckPolicy, ConversationState, ReminderError, ScheduleType
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import WeComIdentity
from app.infrastructure.database.reminder_models import (
    ConversationSession,
    Reminder,
    ReminderRecipient,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class ParsedReminderDraft:
    title: str
    content: str
    scheduled_at: datetime | None
    timezone: str
    schedule_type: ScheduleType = ScheduleType.ONCE
    recurrence_rule: str | None = None
    ambiguous: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "timezone": self.timezone,
            "schedule_type": self.schedule_type.value,
            "recurrence_rule": self.recurrence_rule,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class ConversationReply:
    code: str
    text: str
    reminder_id: str | None = None


_CN_HOURS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


def parse_reminder_text(
    text: str,
    *,
    now: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> ParsedReminderDraft:
    zone = ZoneInfo(timezone)
    instant = (now or datetime.now(UTC)).astimezone(zone)
    original = text.strip().removeprefix("/提醒").strip()
    if not original:
        raise ReminderError("提醒内容不能为空")
    scheduled: datetime | None = None
    ambiguous = False

    relative = re.search(r"(\d+)\s*(分钟|小时|天)后", original)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {
            "分钟": timedelta(minutes=amount),
            "小时": timedelta(hours=amount),
            "天": timedelta(days=amount),
        }[unit]
        scheduled = instant + delta
    else:
        day_offset = 0
        if "后天" in original:
            day_offset = 2
        elif "明天" in original:
            day_offset = 1
        day = instant.date() + timedelta(days=day_offset)
        clock_match = re.search(
            r"(?:(上午|下午|晚上|中午)\s*)?(\d{1,2})(?::(\d{1,2})|点(半|\d{1,2}分?)?)", original
        )
        cn_match = re.search(
            r"(?:(上午|下午|晚上|中午)\s*)?(一|二|两|三|四|五|六|七|八|九|十|十一|十二)点(半)?",
            original,
        )
        if clock_match:
            period, raw_hour, raw_minute, suffix = clock_match.groups()
            hour = int(raw_hour)
            minute = int(raw_minute or (30 if suffix == "半" else str(suffix or "0").rstrip("分")))
            if (period in {"下午", "晚上"} and hour < 12) or (period == "中午" and hour < 11):
                hour += 12
            elif period is None and hour <= 7:
                ambiguous = True
            try:
                scheduled = datetime.combine(day, time(hour, minute), zone)
            except ValueError as exc:
                raise ReminderError("时间格式无效") from exc
        elif cn_match:
            period, raw_hour, half = cn_match.groups()
            hour = _CN_HOURS[raw_hour]
            if (period in {"下午", "晚上"} and hour < 12) or (period == "中午" and hour < 11):
                hour += 12
            elif period is None and hour <= 7:
                ambiguous = True
            scheduled = datetime.combine(day, time(hour, 30 if half else 0), zone)
        else:
            ambiguous = True

    content = re.sub(r"^(请)?提醒我", "", original)
    content = re.sub(
        r"(今天|明天|后天)?\s*(上午|下午|晚上|中午)?\s*[一二两三四五六七八九十\d:点分半]+",
        "",
        content,
    )
    content = re.sub(r"\d+\s*(分钟|小时|天)后", "", content).strip(" ，,。")
    title = content[:200] or "提醒"
    return ParsedReminderDraft(
        title=title,
        content=content,
        scheduled_at=scheduled.astimezone(UTC) if scheduled else None,
        timezone=timezone,
        ambiguous=ambiguous,
    )


class ConversationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reminders: ReminderService,
        *,
        session_ttl: timedelta = timedelta(minutes=10),
        default_timezone: str = "Asia/Shanghai",
    ) -> None:
        self._sessions = session_factory
        self._reminders = reminders
        self._ttl = session_ttl
        self._timezone = default_timezone

    def set_timezone(self, timezone: str) -> None:
        ZoneInfo(timezone)
        self._timezone = timezone

    async def handle_text(
        self,
        *,
        sender_wecom_userid: str,
        text: str,
        now: datetime | None = None,
    ) -> ConversationReply:
        instant = now or datetime.now(UTC)
        identity = await self._identity(sender_wecom_userid)
        if identity is None:
            return ConversationReply("forbidden", "你的企业微信身份尚未关联，无法管理提醒。")
        command = text.strip()
        session = await self._session(identity.id, instant)

        if command in {"/帮助", "帮助"}:
            return ConversationReply(
                "help",
                "命令：/今天、/提醒 <时间和内容>、/取消 <ID>、/完成 <ID>、/稍后 <ID> <分钟>。",
            )
        if command.startswith("/今天"):
            return await self._today(identity.person_id, instant)
        if command.startswith("/完成"):
            return await self._change_owned(command, identity.person_id, "complete")
        if command.startswith("/取消") and len(command.split()) > 1:
            return await self._change_owned(command, identity.person_id, "cancel")
        if command.startswith("/稍后"):
            return await self._snooze_owned(command, identity.person_id, instant)
        if command in {"取消", "/取消"}:
            if session:
                await self._set_session(session, ConversationState.CANCELLED, {}, instant)
            return ConversationReply("cancelled", "已取消当前提醒草稿。")

        if session and session.state == ConversationState.AWAITING_CONFIRMATION.value:
            if command in {"确认", "是", "好的", "创建"}:
                return await self._confirm(session, identity.person_id, instant)
            if command not in {"否", "不", "取消"}:
                return ConversationReply(
                    "confirmation_required", "请回复“确认”创建，或回复“取消”。"
                )

        draft = parse_reminder_text(command, now=instant, timezone=self._timezone)
        if draft.scheduled_at is None or draft.ambiguous:
            await self._upsert_session(
                identity.id, ConversationState.AWAITING_TIME, draft.as_json(), instant
            )
            return ConversationReply(
                "ambiguous_time", "提醒时间不够明确，请写明上午/下午和具体时间。"
            )
        await self._upsert_session(
            identity.id, ConversationState.AWAITING_CONFIRMATION, draft.as_json(), instant
        )
        local = draft.scheduled_at.astimezone(ZoneInfo(draft.timezone))
        return ConversationReply(
            "draft",
            f"将于 {local:%Y-%m-%d %H:%M} 提醒你：{draft.title}。回复“确认”后创建。",
        )

    async def _confirm(
        self, session: ConversationSession, person_id: str, now: datetime
    ) -> ConversationReply:
        draft = session.draft
        scheduled_raw = draft.get("scheduled_at")
        if not isinstance(scheduled_raw, str):
            await self._set_session(session, ConversationState.EXPIRED, {}, now)
            return ConversationReply("expired", "草稿已失效，请重新创建。")
        reminder = await self._reminders.create(
            ReminderCreate(
                creator_person_id=person_id,
                title=str(draft["title"]),
                content=str(draft.get("content", "")),
                schedule_type=ScheduleType(str(draft.get("schedule_type", "once"))),
                timezone=str(draft["timezone"]),
                recipient_ids=(person_id,),
                scheduled_at=datetime.fromisoformat(scheduled_raw),
                recurrence_rule=draft.get("recurrence_rule"),
                require_ack=False,
                ack_policy=AckPolicy.ANY,
            ),
            now=now,
        )
        await self._set_session(session, ConversationState.COMPLETED, {}, now)
        return ConversationReply("created", f"提醒已创建：{reminder.title}", reminder.id)

    async def _identity(self, user_id: str) -> WeComIdentity | None:
        async with self._sessions() as session:
            return cast(
                WeComIdentity | None,
                await session.scalar(
                    select(WeComIdentity).where(
                        WeComIdentity.user_id == user_id, WeComIdentity.active.is_(True)
                    )
                ),
            )

    async def _session(self, identity_id: str, now: datetime) -> ConversationSession | None:
        async with self._sessions() as session, session.begin():
            item = await session.scalar(
                select(ConversationSession).where(
                    ConversationSession.wecom_identity_id == identity_id
                )
            )
            if item and item.expires_at <= now:
                item.state = ConversationState.EXPIRED.value
                item.draft = {}
                item.updated_at = now
                return None
            return item

    async def _upsert_session(
        self,
        identity_id: str,
        state: ConversationState,
        draft: dict[str, Any],
        now: datetime,
    ) -> None:
        async with self._sessions() as session, session.begin():
            item = await session.scalar(
                select(ConversationSession).where(
                    ConversationSession.wecom_identity_id == identity_id
                )
            )
            if item is None:
                item = ConversationSession(
                    id=new_id("cvs"),
                    wecom_identity_id=identity_id,
                    state=state.value,
                    draft=draft,
                    last_message_at=now,
                    expires_at=now + self._ttl,
                    created_at=now,
                    updated_at=now,
                )
                session.add(item)
            else:
                item.state = state.value
                item.draft = draft
                item.last_message_at = now
                item.expires_at = now + self._ttl
                item.updated_at = now

    async def _set_session(
        self,
        item: ConversationSession,
        state: ConversationState,
        draft: dict[str, Any],
        now: datetime,
    ) -> None:
        await self._upsert_session(item.wecom_identity_id, state, draft, now)

    async def _owned_reminder(self, reminder_id: str, person_id: str) -> Reminder | None:
        async with self._sessions() as session:
            return cast(
                Reminder | None,
                await session.scalar(
                    select(Reminder)
                    .outerjoin(ReminderRecipient, ReminderRecipient.reminder_id == Reminder.id)
                    .where(
                        Reminder.id == reminder_id,
                        (
                            (Reminder.creator_person_id == person_id)
                            | (ReminderRecipient.person_id == person_id)
                        ),
                    )
                ),
            )

    async def _change_owned(
        self, command: str, person_id: str, operation: str
    ) -> ConversationReply:
        parts = command.split(maxsplit=1)
        if len(parts) != 2:
            return ConversationReply(
                "invalid_command",
                f"用法：/{'完成' if operation == 'complete' else '取消'} <提醒ID>",
            )
        reminder = await self._owned_reminder(parts[1], person_id)
        if reminder is None:
            return ConversationReply("not_found", "未找到你有权管理的提醒。")
        await getattr(self._reminders, operation)(reminder.id)
        return ConversationReply(
            operation, "提醒已完成。" if operation == "complete" else "提醒已取消。", reminder.id
        )

    async def _snooze_owned(self, command: str, person_id: str, now: datetime) -> ConversationReply:
        parts = command.split()
        if len(parts) not in {2, 3}:
            return ConversationReply("invalid_command", "用法：/稍后 <提醒ID> [分钟]")
        minutes = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else 10
        reminder = await self._owned_reminder(parts[1], person_id)
        if reminder is None:
            return ConversationReply("not_found", "未找到你有权管理的提醒。")
        await self._reminders.snooze(
            reminder.id, until=now + timedelta(minutes=minutes), actor_person_id=person_id
        )
        return ConversationReply("snoozed", f"已延后 {minutes} 分钟。", reminder.id)

    async def _today(self, person_id: str, now: datetime) -> ConversationReply:
        zone = ZoneInfo(self._timezone)
        local = now.astimezone(zone)
        start = datetime.combine(local.date(), time.min, zone).astimezone(UTC)
        end = start + timedelta(days=1)
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(Reminder)
                    .outerjoin(ReminderRecipient, ReminderRecipient.reminder_id == Reminder.id)
                    .where(
                        Reminder.next_run_at >= start,
                        Reminder.next_run_at < end,
                        (
                            (Reminder.creator_person_id == person_id)
                            | (ReminderRecipient.person_id == person_id)
                        ),
                    )
                    .order_by(Reminder.next_run_at)
                )
            )
        if not rows:
            return ConversationReply("today", "今天没有待执行提醒。")
        lines = [
            f"{item.next_run_at.astimezone(zone):%H:%M} {item.title} ({item.id})"
            for item in rows
            if item.next_run_at
        ]
        return ConversationReply("today", "今天的提醒：\n" + "\n".join(lines))
