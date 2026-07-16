from datetime import UTC, datetime
from typing import Protocol

from app.channels.base import ChannelMessage, ChannelResult
from app.channels.wecom.client import WeComClient
from app.config import Settings
from app.media.public_urls import PublicMediaUrlBuilder


class OutboundMedia(Protocol):
    async def media_id(self, asset_id: str, *, now: datetime) -> str: ...


def split_utf8(text: str, max_bytes: int = 2048) -> list[str]:
    if not text:
        return [""]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        char_size = len(char.encode("utf-8"))
        if current and size + char_size > max_bytes:
            parts.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += char_size
    if current:
        parts.append("".join(current))
    return parts


class WeComAdapter:
    def __init__(
        self,
        client: WeComClient,
        settings: Settings,
        media: OutboundMedia | None = None,
        public_media_urls: PublicMediaUrlBuilder | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._media = media
        self._public_media_urls = public_media_urls or PublicMediaUrlBuilder(
            settings.public_base_url,
            settings.public_media_signing_key.get_secret_value(),
        )

    async def send(self, message: ChannelMessage) -> ChannelResult:
        if message.broadcast:
            if not self._settings.allow_broadcast:
                return ChannelResult(False, False, "BROADCAST_FORBIDDEN", "Broadcast is disabled")
            touser = "@all"
        else:
            if not message.recipients:
                return ChannelResult(False, False, "RECIPIENT_INVALID", "Recipient is required")
            touser = "|".join(message.recipients)
        if self._settings.wecom_agent_id is None:
            return ChannelResult(False, False, "AUTH_INVALID", "WeCom agent ID is not configured")
        if message.message_type == "article":
            now = datetime.now(UTC)
            cover_url = message.image_url
            if not cover_url and message.media_asset_id:
                cover_url = self._public_media_urls.signed_image_url(
                    message.media_asset_id,
                    lifetime_seconds=86_400,
                    now=now,
                )
            article_url = message.url or cover_url
            if not article_url:
                return ChannelResult(
                    False,
                    False,
                    "PAYLOAD_INVALID",
                    "Article URL or public image is required",
                )
            payload = {
                "touser": touser,
                "msgtype": "news",
                "agentid": self._settings.wecom_agent_id,
                "news": {
                    "articles": [
                        {
                            "title": message.title,
                            "description": message.content,
                            "url": article_url,
                            "picurl": cover_url or "",
                        }
                    ]
                },
                "enable_duplicate_check": 1,
            }
            return await self._client.send(payload)
        if message.message_type == "template_card":
            action_token = message.payload.get("action_token")
            if not isinstance(action_token, str) or not action_token:
                return ChannelResult(False, False, "PAYLOAD_INVALID", "Action token is required")
            task_id = message.payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                return ChannelResult(False, False, "PAYLOAD_INVALID", "Task ID is required")
            payload = {
                "touser": touser,
                "msgtype": "template_card",
                "agentid": self._settings.wecom_agent_id,
                "template_card": {
                    "card_type": "button_interaction",
                    "task_id": task_id,
                    "main_title": {"title": message.title, "desc": message.content},
                    "button_list": [
                        {
                            "text": "已完成",
                            "key": f"reminder_complete:{action_token}",
                            "style": 1,
                        }
                    ],
                },
                "enable_duplicate_check": 1,
            }
            return await self._client.send(payload)
        if message.message_type in {"image", "voice"}:
            if self._media is None or not message.media_asset_id:
                return ChannelResult(False, False, "MEDIA_INVALID", "Media asset is required")
            try:
                media_id = await self._media.media_id(message.media_asset_id, now=datetime.now(UTC))
            except (OSError, RuntimeError, ValueError):
                return ChannelResult(False, False, "MEDIA_NOT_SENT", "Media preparation failed")
            media_result = await self._client.send(
                {
                    "touser": touser,
                    "msgtype": message.message_type,
                    "agentid": self._settings.wecom_agent_id,
                    message.message_type: {"media_id": media_id},
                    "enable_duplicate_check": 1,
                }
            )
            if not media_result.success or not message.payload.get("interactive_reminder"):
                return media_result
            companion = "\n".join(part for part in [message.title, message.content] if part)
            final = media_result
            for chunk in split_utf8(companion):
                final = await self._client.send(
                    {
                        "touser": touser,
                        "msgtype": "text",
                        "agentid": self._settings.wecom_agent_id,
                        "text": {"content": chunk},
                        "enable_duplicate_check": 1,
                    }
                )
                if not final.success:
                    return final
            return final
        text = "\n".join(part for part in [message.title, message.content, message.url] if part)
        final = ChannelResult(True)
        for chunk in split_utf8(text):
            final = await self._client.send(
                {
                    "touser": touser,
                    "msgtype": "text",
                    "agentid": self._settings.wecom_agent_id,
                    "text": {"content": chunk},
                    "enable_duplicate_check": 1,
                }
            )
            if not final.success:
                return final
        return final

    async def test(self, recipient: str) -> ChannelResult:
        return await self.send(
            ChannelMessage("text", "Notify Hub 测试", "渠道连接正常", [recipient])
        )
