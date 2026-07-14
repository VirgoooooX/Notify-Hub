from datetime import UTC, timedelta

from app.api.dependencies import require_admin
from app.api.errors import AppError
from app.api.schemas import AuthInitialize, AuthLogin, RefreshRequest
from app.application.audit import add_audit
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Admin, RefreshSession
from app.infrastructure.security.tokens import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

router = APIRouter(tags=["admin-auth"])


def token_data(request: Request, admin_id: str, refresh: str) -> dict[str, object]:
    return {
        "access_token": create_access_token(
            admin_id, request.app.state.settings, request.app.state.clock
        ),
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": request.app.state.settings.access_token_minutes * 60,
    }


@router.post("/initialize", status_code=status.HTTP_201_CREATED)
async def initialize(payload: AuthInitialize, request: Request) -> dict[str, object]:
    now = request.app.state.clock.now()
    refresh = create_refresh_token()
    try:
        async with request.app.state.session_factory() as session, session.begin():
            if await session.scalar(select(func.count(Admin.id))):
                raise AppError("already_initialized", "Administrator is already initialized", 409)
            admin = Admin(
                id=new_id("admin"),
                username=payload.username,
                password_hash=hash_password(payload.password),
                active=True,
                singleton_key="primary",
                created_at=now,
                updated_at=now,
            )
            session.add(admin)
            session.add(
                RefreshSession(
                    id=new_id("session"),
                    admin_id=admin.id,
                    token_hash=hash_token(refresh),
                    expires_at=now + timedelta(days=request.app.state.settings.refresh_token_days),
                    revoked_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            add_audit(
                session,
                request.app.state.clock,
                actor_type="admin",
                actor_id=admin.id,
                action="admin.initialize",
                request_id=request.state.request_id,
            )
    except IntegrityError as exc:
        raise AppError("already_initialized", "Administrator is already initialized", 409) from exc
    data = token_data(request, admin.id, refresh)
    data["administrator"] = {"id": admin.id, "name": admin.username}
    return {"data": data, "request_id": request.state.request_id}


@router.get("/status")
async def initialization_status(request: Request) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        initialized = bool(await session.scalar(select(func.count(Admin.id))))
    return {"data": {"initialized": initialized}, "request_id": request.state.request_id}


@router.post("/login")
async def login(payload: AuthLogin, request: Request) -> dict[str, object]:
    client_ip = request.client.host if request.client else "unknown"
    limiter_key = f"{client_ip}:{payload.username.casefold()}"
    settings = request.app.state.settings
    if not await request.app.state.login_limiter.allow(
        limiter_key, settings.login_max_attempts, settings.login_window_seconds
    ):
        raise AppError("rate_limited", "Too many login attempts", 429)
    now = request.app.state.clock.now()
    async with request.app.state.session_factory() as session:
        admin = await session.scalar(select(Admin).where(Admin.username == payload.username))
        if (
            admin is None
            or not admin.active
            or not verify_password(payload.password, admin.password_hash)
        ):
            add_audit(
                session,
                request.app.state.clock,
                actor_type="anonymous",
                actor_id=None,
                action="admin.login_failed",
                request_id=request.state.request_id,
                details={"username": payload.username},
            )
            await session.commit()
            raise AppError("invalid_credentials", "Invalid username or password", 401)
        refresh = create_refresh_token()
        session.add(
            RefreshSession(
                id=new_id("session"),
                admin_id=admin.id,
                token_hash=hash_token(refresh),
                expires_at=now + timedelta(days=settings.refresh_token_days),
                revoked_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="admin.login",
            request_id=request.state.request_id,
        )
        await session.commit()
    return {"data": token_data(request, admin.id, refresh), "request_id": request.state.request_id}


@router.post("/refresh")
async def refresh(payload: RefreshRequest, request: Request) -> dict[str, object]:
    now = request.app.state.clock.now()
    async with request.app.state.session_factory() as session, session.begin():
        old = await session.scalar(
            select(RefreshSession).where(
                RefreshSession.token_hash == hash_token(payload.refresh_token)
            )
        )
        expires_at = old.expires_at if old is not None else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if old is None or old.revoked_at is not None or expires_at is None or expires_at <= now:
            raise AppError("invalid_refresh_token", "Refresh token is invalid or expired", 401)
        claimed = await session.execute(
            update(RefreshSession)
            .where(
                RefreshSession.id == old.id,
                RefreshSession.revoked_at.is_(None),
                RefreshSession.expires_at > now,
            )
            .values(revoked_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise AppError("invalid_refresh_token", "Refresh token is invalid or expired", 401)
        new_token = create_refresh_token()
        session.add(
            RefreshSession(
                id=new_id("session"),
                admin_id=old.admin_id,
                token_hash=hash_token(new_token),
                expires_at=now + timedelta(days=request.app.state.settings.refresh_token_days),
                revoked_at=None,
                created_at=now,
                updated_at=now,
            )
        )
    return {
        "data": token_data(request, old.admin_id, new_token),
        "request_id": request.state.request_id,
    }


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    payload: RefreshRequest | None = None,
    admin: Admin = Depends(require_admin),
) -> None:
    now = request.app.state.clock.now()
    async with request.app.state.session_factory() as session, session.begin():
        criteria = [RefreshSession.admin_id == admin.id, RefreshSession.revoked_at.is_(None)]
        if payload is not None:
            criteria.append(RefreshSession.token_hash == hash_token(payload.refresh_token))
        await session.execute(
            update(RefreshSession).where(*criteria).values(revoked_at=now, updated_at=now)
        )
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="admin.logout",
            request_id=request.state.request_id,
        )


@router.get("/me")
async def me(request: Request, admin: Admin = Depends(require_admin)) -> dict[str, object]:
    return {
        "data": {"id": admin.id, "username": admin.username},
        "request_id": request.state.request_id,
    }
