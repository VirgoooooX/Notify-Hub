from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from app.channels.base import ChannelMessage, ChannelResult
from app.channels.wecom.adapter import WeComAdapter, split_utf8
from app.channels.wecom.client import WeComClient
from app.config import Settings
from app.infrastructure.logging.setup import redact
from app.infrastructure.security.tokens import verify_media_signature
from pydantic import ValidationError


class RecordingWeComClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def send(self, payload: dict[str, object]) -> ChannelResult:
        self.payloads.append(payload)
        return ChannelResult(True, provider_message_id=f"message-{len(self.payloads)}")


class StaticMedia:
    async def media_id(self, asset_id: str, *, now: datetime) -> str:
        assert asset_id == "asset-1"
        assert now.tzinfo is not None
        return "media-1"


@pytest.mark.asyncio
async def test_interactive_image_sends_image_then_plain_text_menu_hint() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
    )
    client = RecordingWeComClient()
    adapter = WeComAdapter(client, settings, StaticMedia())  # type: ignore[arg-type]

    result = await adapter.send(
        ChannelMessage(
            "image",
            "提交月度报表",
            "🔁【持续提醒｜需要你确认完成】\n这不是一次性通知；在你完成前，系统会按设定间隔继续提醒。\n完成后请尽快点击底部【快捷操作】→【完成本次】。\n菜单默认操作最近收到的一条交互式提醒。",
            ["user-1"],
            payload={"interactive_reminder": True},
            media_asset_id="asset-1",
        )
    )

    assert result.success
    assert [payload["msgtype"] for payload in client.payloads] == ["image", "text"]
    assert "提交月度报表" in client.payloads[1]["text"]["content"]  # type: ignore[index]
    assert "【快捷操作】" in client.payloads[1]["text"]["content"]  # type: ignore[index]
    assert "最近收到的一条交互式提醒" in client.payloads[1]["text"]["content"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_interactive_article_uses_news_and_keeps_menu_hint() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
    )
    client = RecordingWeComClient()
    adapter = WeComAdapter(client, settings)  # type: ignore[arg-type]

    result = await adapter.send(
        ChannelMessage(
            "article",
            "提交月度报表",
            "正文\n\n🔁【持续提醒｜需要你确认完成】\n这不是一次性通知；在你完成前，系统会按设定间隔继续提醒。\n完成后请尽快点击底部【快捷操作】→【完成本次】。\n菜单默认操作最近收到的一条交互式提醒。",
            ["user-1"],
            url="https://example.com/report",
            image_url="https://example.com/report.png",
            payload={"interactive_reminder": True},
        )
    )

    assert result.success
    assert len(client.payloads) == 1
    assert client.payloads[0]["msgtype"] == "news"
    article = client.payloads[0]["news"]["articles"][0]  # type: ignore[index]
    assert article["title"] == "提交月度报表"
    assert "【快捷操作】" in article["description"]
    assert "最近收到的一条交互式提醒" in article["description"]


@pytest.mark.asyncio
async def test_article_uses_signed_local_media_as_cover_and_click_url() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        public_base_url="https://notify.example.com",
        public_media_signing_key="signed-media-key",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
    )
    client = RecordingWeComClient()
    adapter = WeComAdapter(client, settings)  # type: ignore[arg-type]

    result = await adapter.send(
        ChannelMessage(
            "article",
            "带药",
            "出门前检查",
            ["user-1"],
            media_asset_id="med_cover",
        )
    )

    assert result.success
    article = client.payloads[0]["news"]["articles"][0]  # type: ignore[index]
    assert article["url"] == article["picurl"]
    parsed = urlsplit(article["picurl"])
    query = parse_qs(parsed.query)
    expires = int(query["expires"][0])
    assert parsed.path == "/public/media/med_cover"
    assert verify_media_signature("med_cover", expires, query["sig"][0], "signed-media-key")


@pytest.mark.asyncio
async def test_invaliduser_is_not_treated_as_successful_delivery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gettoken"):
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        return httpx.Response(200, json={"errcode": 0, "invaliduser": "missing-user"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://wecom.test/")
    settings = Settings(
        _env_file=None,
        environment="test",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
        wecom_api_base_url="https://wecom.test",
    )
    result = await WeComClient(settings, FixedClock(), http).send(
        {"touser": "missing-user", "msgtype": "text", "text": {"content": "test"}}
    )

    assert not result.success
    assert result.error_code == "RECIPIENT_INVALID"
    assert result.response_metadata["invalid_user_ids"] == ["missing-user"]
    await http.aclose()


@pytest.mark.asyncio
async def test_menu_publish_refreshes_invalid_token_once() -> None:
    token_calls = 0
    menu_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, menu_calls
        if request.url.path.endswith("gettoken"):
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "access_token": f"token-{token_calls}",
                    "expires_in": 7200,
                },
            )
        menu_calls += 1
        assert request.url.path.endswith("cgi-bin/menu/create")
        assert request.url.params["agentid"] == "1"
        return httpx.Response(200, json={"errcode": 40014 if menu_calls == 1 else 0})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://wecom.test/")
    settings = Settings(
        _env_file=None,
        environment="test",
        wecom_corp_id="corp",
        wecom_agent_id=1,
        wecom_secret="secret",
        wecom_api_base_url="https://wecom.test",
    )
    result = await WeComClient(settings, FixedClock(), http).create_menu(
        {"button": [{"name": "快捷操作"}]}
    )

    assert result.success
    assert token_calls == 2
    assert menu_calls == 2
    await http.aclose()


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
    updated = await client.update_template_card(response_code="callback-code", user_ids=["u1"])
    assert (
        first.success and second.success and third.success and updated.success and token_calls == 1
    )
    assert sent[0]["touser"] == "u1|u2"
    assert sent[2]["template_card"]["task_id"] == "act_example"
    assert sent[3] == {"userids": ["u1"], "response_code": "callback-code"}
    assert "sensitive" not in str(sent)
    assert paths == [
        "/proxy/cgi-bin/gettoken",
        "/proxy/cgi-bin/message/send",
        "/proxy/cgi-bin/message/send",
        "/proxy/cgi-bin/message/send",
        "/proxy/cgi-bin/message/update_template_card",
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
