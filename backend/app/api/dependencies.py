import secrets
from collections.abc import AsyncIterator
from typing import cast

import jwt
from app.api.errors import AppError
from app.infrastructure.database.models import Admin, ApiClient
from app.infrastructure.security.tokens import decode_access_token, hash_token
from fastapi import Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


async def require_admin(
    request: Request, authorization: str | None = Header(default=None)
) -> Admin:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError("unauthorized", "Administrator authentication required", 401)
    raw_token = authorization[7:].strip()
    settings = request.app.state.settings
    if settings.admin_api_key is not None and secrets.compare_digest(
        raw_token, settings.admin_api_key.get_secret_value()
    ):
        async with request.app.state.session_factory() as session:
            admin = await session.scalar(
                select(Admin).where(Admin.active.is_(True), Admin.singleton_key == "primary")
            )
            if admin is None:
                raise AppError("unauthorized", "Administrator account is unavailable", 401)
            request.state.admin_id = admin.id
            return cast(Admin, admin)
    try:
        admin_id = decode_access_token(raw_token, settings)
    except jwt.PyJWTError:
        raise AppError("unauthorized", "Invalid or expired administrator token", 401) from None
    async with request.app.state.session_factory() as session:
        admin = await session.get(Admin, admin_id)
        if admin is None or not admin.active:
            raise AppError("unauthorized", "Administrator account is unavailable", 401)
        request.state.admin_id = admin.id
        return cast(Admin, admin)


async def _resolve_article_actor(
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
) -> Admin | ApiClient:
    raw_token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[7:].strip()
    elif x_api_key:
        raw_token = x_api_key.strip()

    if not raw_token:
        raise AppError("unauthorized", "Authentication required", 401)

    settings = request.app.state.settings

    matches_admin_key = settings.admin_api_key is not None and secrets.compare_digest(
        raw_token, settings.admin_api_key.get_secret_value()
    )
    if matches_admin_key:
        async with request.app.state.session_factory() as session:
            admin = await session.scalar(
                select(Admin).where(Admin.active.is_(True), Admin.singleton_key == "primary")
            )
            if admin is None:
                raise AppError("unauthorized", "Administrator account is unavailable", 401)
            request.state.admin_id = admin.id
            return cast(Admin, admin)

    if raw_token.startswith("nfy_"):
        key_hash = hash_token(raw_token)
        async with request.app.state.session_factory() as session:
            client = await session.scalar(select(ApiClient).where(ApiClient.key_hash == key_hash))
            if client is None or client.revoked_at is not None:
                raise AppError("unauthorized", "API key is invalid or revoked", 401)
            limiter = request.app.state.api_limiter
            allowed = await limiter.allow(client.id, client.rate_limit_per_minute, 60)
            if not allowed:
                raise AppError("rate_limited", "API client rate limit exceeded", 429)
            return cast(ApiClient, client)

    try:
        admin_id = decode_access_token(raw_token, settings)
    except jwt.PyJWTError:
        raise AppError("unauthorized", "Invalid or expired administrator token", 401) from None
    async with request.app.state.session_factory() as session:
        admin = await session.get(Admin, admin_id)
        if admin is None or not admin.active:
            raise AppError("unauthorized", "Administrator account is unavailable", 401)
        request.state.admin_id = admin.id
        return cast(Admin, admin)


async def require_article_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Admin | ApiClient:
    return await _resolve_article_actor(request, authorization, x_api_key)


async def require_api_client(
    request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")
) -> ApiClient:
    if not x_api_key or not x_api_key.startswith("nfy_"):
        raise AppError("unauthorized", "A valid API key is required", 401)
    key_hash = hash_token(x_api_key)
    async with request.app.state.session_factory() as session:
        client = await session.scalar(select(ApiClient).where(ApiClient.key_hash == key_hash))
        if client is None or client.revoked_at is not None:
            raise AppError("unauthorized", "API key is invalid or revoked", 401)
        limiter = request.app.state.api_limiter
        allowed = await limiter.allow(client.id, client.rate_limit_per_minute, 60)
        if not allowed:
            raise AppError("rate_limited", "API client rate limit exceeded", 429)
        return cast(ApiClient, client)
