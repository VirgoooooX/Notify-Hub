from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.channels.wecom.crypto import WeComCrypto, WeComCryptoError
from defusedxml import ElementTree


class WeComCallbackError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IncomingCallback:
    sender_external_id: str
    provider_message_id: str | None
    message_type: str
    text: str | None
    media_refs: dict[str, str]
    event: str | None
    event_key: str | None
    action_token: str | None
    response_code: str | None
    received_at: datetime
    dedupe_key: str


def _elements(xml: str) -> Mapping[str, str]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise WeComCallbackError("invalid callback XML") from exc
    values: dict[str, str] = {}
    for child in root:
        if child.tag and child.text is not None:
            values[str(child.tag)] = child.text
    return values


def encrypted_value(body: bytes, *, max_body_bytes: int = 1_048_576) -> str:
    if len(body) > max_body_bytes:
        raise WeComCallbackError("callback body is too large")
    try:
        values = _elements(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WeComCallbackError("callback body must be UTF-8") from exc
    encrypted = values.get("Encrypt")
    if not encrypted:
        raise WeComCallbackError("callback Encrypt field is missing")
    return encrypted


def verify_url(
    crypto: WeComCrypto,
    *,
    signature: str,
    timestamp: str,
    nonce: str,
    echo: str,
    now: int | None = None,
) -> str:
    crypto.verify(
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=echo,
        now=now,
    )
    return crypto.decrypt(echo)


def parse_callback(
    crypto: WeComCrypto,
    *,
    signature: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    now: int | None = None,
    max_body_bytes: int = 1_048_576,
) -> IncomingCallback:
    encrypted = encrypted_value(body, max_body_bytes=max_body_bytes)
    crypto.verify(
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypted=encrypted,
        now=now,
    )
    values = _elements(crypto.decrypt(encrypted))
    sender = values.get("FromUserName")
    message_type = values.get("MsgType")
    if not sender or not message_type:
        raise WeComCallbackError("callback sender or message type is missing")
    provider_id = values.get("MsgId")
    event_key = values.get("EventKey")
    action_token = (
        event_key.removeprefix("reminder_complete:")
        if (event_key and event_key.startswith("reminder_complete:"))
        else None
    )
    stable = provider_id or "|".join(
        (
            sender,
            message_type,
            values.get("CreateTime", timestamp),
            event_key or "",
            values.get("Content", ""),
        )
    )
    dedupe_key = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    media_refs = {
        key: value
        for key, value in values.items()
        if key in {"MediaId", "PicUrl", "Format", "Recognition"}
    }
    return IncomingCallback(
        sender_external_id=sender,
        provider_message_id=provider_id,
        message_type=message_type,
        text=values.get("Content") or values.get("Recognition"),
        media_refs=media_refs,
        event=values.get("Event"),
        event_key=event_key,
        action_token=action_token,
        response_code=values.get("ResponseCode"),
        received_at=datetime.now(UTC),
        dedupe_key=dedupe_key,
    )


__all__ = [
    "IncomingCallback",
    "WeComCallbackError",
    "WeComCryptoError",
    "encrypted_value",
    "parse_callback",
    "verify_url",
]
