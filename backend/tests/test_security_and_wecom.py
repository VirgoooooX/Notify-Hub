from datetime import UTC, datetime

import httpx
import pytest
from app.channels.wecom.adapter import WeComAdapter, split_utf8
from app.channels.wecom.client import WeComClient
from app.config import Settings
from app.infrastructure.logging.setup import redact


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 14, tzinfo=UTC)


def test_log_redaction_and_utf8_chunking() -> None:
    assert redact({"Authorization": "Bearer abc", "password": "secret"}) == {
        "Authorization": "[REDACTED]",
        "password": "[REDACTED]",
    }
    chunks = split_utf8("你" * 1000, 100)
    assert "".join(chunks) == "你" * 1000
    assert all(len(chunk.encode()) <= 100 for chunk in chunks)


@pytest.mark.asyncio
async def test_wecom_token_cache_and_payload() -> None:
    token_calls = 0
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("gettoken"):
            token_calls += 1
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "sensitive", "expires_in": 7200}
            )
        sent.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"errcode": 0, "msgid": "m1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://wecom.test")
    settings = Settings(
        environment="test",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
        wecom_api_base_url="https://wecom.test",
    )
    client = WeComClient(settings, FixedClock(), http)
    adapter = WeComAdapter(client, settings)
    from app.channels.base import ChannelMessage

    first = await adapter.send(ChannelMessage("text", "title", "body", ["u1", "u2"]))
    second = await adapter.send(ChannelMessage("text", "title", "body", ["u1"]))
    assert first.success and second.success and token_calls == 1
    assert sent[0]["touser"] == "u1|u2"
    assert "sensitive" not in str(sent)
    await http.aclose()
