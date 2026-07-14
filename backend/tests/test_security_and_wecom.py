from datetime import UTC, datetime

import httpx
import pytest
from app.channels.wecom.adapter import WeComAdapter, split_utf8
from app.channels.wecom.client import WeComClient
from app.config import Settings
from app.infrastructure.logging.setup import redact
from pydantic import ValidationError


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 14, tzinfo=UTC)


def test_log_redaction_and_utf8_chunking() -> None:
    assert redact({"Authorization": "Bearer abc", "password": "secret"}) == {
        "Authorization": "[REDACTED]",
        "password": "[REDACTED]",
    }
    assert redact({"phone": "private", "corp_id": "private"}) == {
        "phone": "[REDACTED]",
        "corp_id": "[REDACTED]",
    }
    chunks = split_utf8("你" * 1000, 100)
    assert "".join(chunks) == "你" * 1000
    assert all(len(chunk.encode()) <= 100 for chunk in chunks)


@pytest.mark.asyncio
async def test_wecom_token_cache_and_payload() -> None:
    token_calls = 0
    sent: list[dict[str, object]] = []
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        paths.append(request.url.path)
        if request.url.path.endswith("gettoken"):
            token_calls += 1
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "sensitive", "expires_in": 7200}
            )
        sent.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"errcode": 0, "msgid": "m1"})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://wecom.test/proxy/"
    )
    settings = Settings(
        _env_file=None,
        environment="test",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
        wecom_api_base_url="https://wecom.test/proxy",
    )
    client = WeComClient(settings, FixedClock(), http)
    adapter = WeComAdapter(client, settings)
    from app.channels.base import ChannelMessage

    first = await adapter.send(ChannelMessage("text", "title", "body", ["u1", "u2"]))
    second = await adapter.send(ChannelMessage("text", "title", "body", ["u1"]))
    third = await adapter.send(
        ChannelMessage(
            "template_card",
            "interactive title",
            "interactive body",
            ["u1"],
            payload={"task_id": "act_example", "action_token": "token"},
        )
    )
    assert first.success and second.success and third.success and token_calls == 1
    assert sent[0]["touser"] == "u1|u2"
    assert sent[2]["template_card"]["task_id"] == "act_example"
    assert "sensitive" not in str(sent)
    assert paths == [
        "/proxy/cgi-bin/gettoken",
        "/proxy/cgi-bin/message/send",
        "/proxy/cgi-bin/message/send",
        "/proxy/cgi-bin/message/send",
    ]
    await http.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "http://proxy.example.com",
        "https://user:password@proxy.example.com",
        "https://proxy.example.com?target=wecom",
        "https://proxy.example.com/#fragment",
        "https://",
    ],
)
def test_wecom_proxy_url_rejects_unsafe_values(url: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, environment="test", wecom_api_base_url=url)


def test_wecom_proxy_url_accepts_https_path_prefix() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        wecom_api_base_url="https://proxy.example.com/wecom/",
    )
    assert settings.wecom_api_base_url == "https://proxy.example.com/wecom"


def test_production_rejects_weak_or_partial_secret_configuration() -> None:
    strong = "x" * 32
    with pytest.raises(ValidationError, match="encryption key"):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=strong,
            public_media_signing_key=strong,
            secret_encryption_key="",
        )
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=strong,
            public_media_signing_key=strong,
            secret_encryption_key=strong,
            wecom_corp_id="corp-only",
            wecom_agent_id=None,
            wecom_secret=None,
        )
    with pytest.raises(ValidationError, match="callback Token"):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=strong,
            public_media_signing_key=strong,
            secret_encryption_key=strong,
            wecom_corp_id=None,
            wecom_agent_id=None,
            wecom_secret=None,
            wecom_callback_token="token-only",
            wecom_callback_aes_key=None,
        )
