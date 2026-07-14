from __future__ import annotations

from typing import cast

from app.api.errors import AppError
from app.channels.wecom.callback import WeComCallbackError, parse_callback, verify_url
from app.channels.wecom.crypto import WeComCrypto, WeComCryptoError
from fastapi import APIRouter, Query, Request, Response

router = APIRouter(tags=["wecom-callback"])


def _crypto(request: Request) -> WeComCrypto:
    crypto = getattr(request.app.state, "wecom_callback_crypto", None)
    if crypto is None:
        raise AppError("channel_not_configured", "WeCom callback is not configured", 503)
    return cast(WeComCrypto, crypto)


@router.get("/channels/wecom/callback")
async def verify_wecom_callback(
    request: Request,
    msg_signature: str = Query(min_length=1),
    timestamp: str = Query(min_length=1),
    nonce: str = Query(min_length=1),
    echostr: str = Query(min_length=1),
) -> Response:
    try:
        echo = verify_url(
            _crypto(request),
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echo=echostr,
        )
    except (WeComCryptoError, WeComCallbackError) as exc:
        raise AppError("invalid_callback", "Invalid WeCom callback", 403) from exc
    return Response(content=echo, media_type="text/plain")


@router.post("/channels/wecom/callback")
async def receive_wecom_callback(
    request: Request,
    msg_signature: str = Query(min_length=1),
    timestamp: str = Query(min_length=1),
    nonce: str = Query(min_length=1),
) -> Response:
    max_body_bytes = getattr(request.app.state.settings, "wecom_callback_max_body_bytes", 1_048_576)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_body_bytes:
        raise AppError("callback_too_large", "WeCom callback body is too large", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_body_bytes:
            raise AppError("callback_too_large", "WeCom callback body is too large", 413)
        body.extend(chunk)
    try:
        callback = parse_callback(
            _crypto(request),
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            body=bytes(body),
            max_body_bytes=max_body_bytes,
        )
        await request.app.state.wecom_callback_service.accept(callback)
    except (WeComCryptoError, WeComCallbackError) as exc:
        raise AppError("invalid_callback", "Invalid WeCom callback", 403) from exc
    return Response(content="success", media_type="text/plain")
