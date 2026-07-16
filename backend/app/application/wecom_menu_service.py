from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from app.application.mobile_identity_service import MobileIdentityService
from app.application.reminder_service import (
    ReminderNotFound,
    ReminderPermissionDenied,
    ReminderService,
)

MENU_COMPLETE = "nh.reminders.complete_latest"
MENU_SNOOZE_10 = "nh.reminders.snooze_10_latest"
MENU_SNOOZE_30 = "nh.reminders.snooze_30_latest"
MENU_IGNORE_TODAY = "nh.reminders.ignore_today_latest"
MENU_STOP = "nh.reminders.stop_latest"
MENU_CREATE_TEXT = "nh.reminders.create_text"
MENU_CREATE_ARTICLE = "nh.reminders.create_article"
MENU_CREATE_FULL = "nh.reminders.create_full"
MENU_LIST_AWAITING = "nh.reminders.list_awaiting"
MENU_LIST_TODAY = "nh.reminders.list_today"
MENU_LIST_ALL = "nh.reminders.list_all"


@dataclass(frozen=True, slots=True)
class MenuResult:
    code: str
    text: str


def build_wecom_menu_payload() -> dict[str, object]:
    """Return the complete three-series menu for WeCom's create-menu API."""
    return {
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


class WeComMenuService:
    def __init__(
        self,
        reminders: ReminderService,
        mobile_identity: MobileIdentityService | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self._reminders = reminders
        self._mobile_identity = mobile_identity
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None

    async def _mobile_link(
        self,
        sender_user_id: str,
        *,
        path: str,
        label: str,
        query: dict[str, str] | None = None,
    ) -> MenuResult:
        if self._mobile_identity is None or self._public_base_url is None:
            return MenuResult(
                "mobile_not_configured",
                "移动提醒入口尚未配置，请管理员设置 NOTIFY_HUB_PUBLIC_BASE_URL。",
            )
        identity = await self._mobile_identity.identity_for_user(sender_user_id)
        if identity is None:
            return MenuResult("forbidden", "你的企业微信身份尚未关联，无法查看提醒。")
        parameters = {**(query or {}), "entry": self._mobile_identity.issue(identity.id)}
        url = f"{self._public_base_url}{path}?{urlencode(parameters)}"
        return MenuResult("mobile_link", f"{label}\n\n{url}\n\n该链接为当前账号生成，请勿转发。")

    async def handle(
        self,
        sender_user_id: str,
        event_key: str,
        *,
        incoming_message_id: str | None = None,
    ) -> MenuResult:
        if event_key == MENU_CREATE_TEXT:
            return MenuResult(
                "create_text",
                "请直接发送提醒内容，例如：\n\n明天下午3点提醒我提交月度报表\n\n我会先生成草稿，等你确认后再创建。",
            )
        if event_key == MENU_CREATE_ARTICLE:
            return await self._mobile_link(
                sender_user_id,
                path="/m/reminders/new",
                query={"content": "article"},
                label="点击打开图文提醒创建页：",
            )
        if event_key == MENU_CREATE_FULL:
            return await self._mobile_link(
                sender_user_id,
                path="/m/reminders/new",
                label="点击打开完整提醒创建页：",
            )
        if event_key == MENU_LIST_AWAITING:
            return await self._mobile_link(
                sender_user_id,
                path="/m/reminders/active",
                query={"scope": "awaiting_ack"},
                label="点击查看等待你完成的提醒：",
            )
        if event_key == MENU_LIST_TODAY:
            return await self._mobile_link(
                sender_user_id,
                path="/m/reminders/active",
                query={"scope": "today"},
                label="点击查看今天的提醒：",
            )
        if event_key == MENU_LIST_ALL:
            return await self._mobile_link(
                sender_user_id,
                path="/m/reminders/active",
                query={"scope": "all"},
                label="点击查看全部提醒：",
            )

        operations = {
            MENU_COMPLETE: "complete",
            MENU_SNOOZE_10: "snooze_10",
            MENU_SNOOZE_30: "snooze_30",
            MENU_IGNORE_TODAY: "ignore_today",
            MENU_STOP: "stop",
        }
        operation = operations.get(event_key)
        if operation is None:
            return MenuResult("unknown_menu", "这个菜单项暂不受支持，请刷新企业微信菜单。")
        try:
            result = await self._reminders.operate_latest_interactive(
                sender_wecom_userid=sender_user_id,
                operation=operation,
                incoming_message_id=incoming_message_id,
            )
        except ReminderPermissionDenied:
            return MenuResult("forbidden", "你的企业微信身份尚未关联，无法操作提醒。")
        except ReminderNotFound:
            return MenuResult("not_found", "当前没有可操作的交互式提醒。")

        title = result.title
        messages = {
            "completed": f"✅ 已完成：{title}，本次持续提醒已停止。",
            "snooze_10": f"⏰ 已推迟10分钟：{title}，10分钟内不会再次催办。",
            "snooze_30": f"⏰ 已推迟30分钟：{title}，30分钟内不会再次催办。",
            "ignored_today": f"🌙 今日已忽略：{title}，今天不会再次催办。",
            "stopped": f"⏹️ 已停止本次：{title}，不影响后续周期提醒。",
            "not_active": (
                f"ℹ️ 当前没有待你处理的提醒。\n\n最近收到的“{title}”已经结束，已从快捷操作中移除。"
            ),
        }
        return MenuResult(result.code, messages[result.code])
