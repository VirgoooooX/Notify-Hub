from __future__ import annotations

from dataclasses import replace
from typing import cast

from app.api.errors import AppError
from app.channels.wecom.callback import WeComCallbackError, parse_callback, verify_url
from app.channels.wecom.crypto import WeComCrypto, WeComCryptoError
from app.infrastructure.database.profile_models import WeComProfileConfig
from app.profiles.constants import DEFAULT_PROFILE_ID
from app.profiles.errors import ProfileError
from fastapi import APIRouter, Query, Request, Response

router = APIRouter(tags=["wecom-callback"])


def _crypto(request: Request) -> WeComCrypto:
    crypto = getattr(request.app.state, "wecom_callback_crypto", None)
    if crypto is None:
        raise AppError("channel_not_configured", "WeCom callback is not configured", 503)
    return cast(WeComCrypto, crypto)


async def _default_profile_crypto(request: Request) -> WeComCrypto:
    try:
        profile = await request.app.state.profile_registry.resolve(
            DEFAULT_PROFILE_ID, allow_disabled=True
        )
    except ProfileError as exc:
        raise AppError(exc.code.lower(), exc.message, 409) from exc
    try:
        crypto = await request.app.state.profile_registry.callback_crypto(profile.id)
        return cast(WeComCrypto, crypto)
    except ProfileError as exc:
        compatibility_crypto = getattr(request.app.state, "wecom_callback_crypto", None)
        if compatibility_crypto is not None:
            async with request.app.state.session_factory() as session:
                config = await session.get(WeComProfileConfig, DEFAULT_PROFILE_ID)
            if (
                profile.enabled
                and profile.capabilities.callback_enabled
                and (config is None or config.callback_enabled)
            ):
                # Preserve the legacy ENV/test injection path for the default
                # Profile only; named Profiles never use this fallback.
                return cast(WeComCrypto, compatibility_crypto)
        raise AppError(exc.code.lower(), exc.message, 409) from exc


async def _profile_crypto(request: Request, profile_key: str) -> tuple[str, WeComCrypto]:
    try:
        profile = await request.app.state.profile_registry.resolve(profile_key)
        crypto = await request.app.state.profile_registry.callback_crypto(profile.id)
    except ProfileError as exc:
        raise AppError(exc.code.lower(), exc.message, 409) from exc
    return profile.id, crypto


def _invalid_callback(exc: Exception) -> AppError:
    return AppError("invalid_callback", "Invalid WeCom callback", 403)


async def _read_callback_body(request: Request) -> bytes:
    max_body_bytes = getattr(request.app.state.settings, "wecom_callback_max_body_bytes", 1_048_576)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_body_bytes:
        raise AppError("callback_too_large", "WeCom callback body is too large", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_body_bytes:
            raise AppError("callback_too_large", "WeCom callback body is too large", 413)
        body.extend(chunk)
    return bytes(body)


async def _verify(
    request: Request,
    crypto: WeComCrypto,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> Response:
    try:
        echo = verify_url(
            crypto,
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echo=echostr,
        )
    except (WeComCryptoError, WeComCallbackError) as exc:
        raise _invalid_callback(exc) from exc
    return Response(content=echo, media_type="text/plain")


async def _receive(
    request: Request,
    crypto: WeComCrypto,
    profile_id: str,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> Response:
    body = await _read_callback_body(request)
    max_body_bytes = getattr(request.app.state.settings, "wecom_callback_max_body_bytes", 1_048_576)
    try:
        callback = parse_callback(
            crypto,
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
            max_body_bytes=max_body_bytes,
        )
        await request.app.state.wecom_callback_service.accept(
            replace(callback, profile_id=profile_id)
        )
    except (WeComCryptoError, WeComCallbackError) as exc:
        raise _invalid_callback(exc) from exc
    return Response(content="success", media_type="text/plain")


@router.get("/channels/wecom/callback")
async def verify_wecom_callback(
    request: Request,
    msg_signature: str = Query(min_length=1),
    timestamp: str = Query(min_length=1),
    nonce: str = Query(min_length=1),
    echostr: str = Query(min_length=1),
) -> Response:
    return await _verify(
        request,
        await _default_profile_crypto(request),
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr=echostr,
    )


@router.get("/channels/wecom/{profile_key}/callback")
async def verify_profile_wecom_callback(
    profile_key: str,
    request: Request,
    msg_signature: str = Query(min_length=1),
    timestamp: str = Query(min_length=1),
    nonce: str = Query(min_length=1),
    echostr: str = Query(min_length=1),
) -> Response:
    _profile_id, crypto = await _profile_crypto(request, profile_key)
    return await _verify(
        request,
        crypto,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr=echostr,
    )


@router.post("/channels/wecom/callback")
async def receive_wecom_callback(
    request: Request,
    msg_signature: str = Query(min_length=1),
    timestamp: str = Query(min_length=1),
    nonce: str = Query(min_length=1),
) -> Response:
    return await _receive(
        request,
        await _default_profile_crypto(request),
        DEFAULT_PROFILE_ID,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
    )


@router.post("/channels/wecom/{profile_key}/callback")
async def receive_profile_wecom_callback(
    profile_key: str,
    request: Request,
    msg_signature: str = Query(min_length=1),
    timestamp: str = Query(min_length=1),
    nonce: str = Query(min_length=1),
) -> Response:
    profile_id, crypto = await _profile_crypto(request, profile_key)
    return await _receive(
        request,
        crypto,
        profile_id,
        msg_signature=msg_signature,
        timestamp=timestamp,
        nonce=nonce,
    )
