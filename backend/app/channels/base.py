from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ChannelMessage:
    message_type: str
    title: str
    content: str
    recipients: list[str]
    url: str | None = None
    image_url: str | None = None
    broadcast: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    media_asset_id: str | None = None


@dataclass(frozen=True)
class ChannelResult:
    success: bool
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    provider_message_id: str | None = None
    provider_status: int | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)


class NotificationChannel(Protocol):
    async def send(self, message: ChannelMessage) -> ChannelResult: ...

    async def test(self, recipient: str) -> ChannelResult: ...


class FakeChannel:
    def __init__(self, result: ChannelResult | None = None) -> None:
        self.result = result or ChannelResult(success=True, provider_message_id="fake-message")
        self.messages: list[ChannelMessage] = []

    async def send(self, message: ChannelMessage) -> ChannelResult:
        self.messages.append(message)
        return self.result

    async def test(self, recipient: str) -> ChannelResult:
        return await self.send(ChannelMessage("text", "Notify Hub", "test", [recipient]))


class UnconfiguredChannel:
    async def send(self, _message: ChannelMessage) -> ChannelResult:
        return ChannelResult(
            success=False,
            retryable=False,
            error_code="CHANNEL_NOT_CONFIGURED",
            error_message="WeCom channel is not configured",
        )

    async def test(self, _recipient: str) -> ChannelResult:
        return await self.send(ChannelMessage("text", "", "", []))
