from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.dependencies import require_admin
from app.application.audit import add_audit
from app.infrastructure.database.models import (
    Admin,
    Delivery,
    DeliveryStatus,
    Event,
    PlatformSetting,
)
from app.infrastructure.database.plugin_models import PluginRecord
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

router = APIRouter(tags=["admin-management"])


class SettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, max_length=100)
    retention_days: int | None = Field(default=None, ge=7, le=3650)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("unknown IANA timezone") from exc
        return value


async def _setting(request: Request, key: str, default: object) -> object:
    async with request.app.state.session_factory() as session:
        row = await session.get(PlatformSetting, key)
        return default if row is None else row.value


async def _write_setting(request: Request, key: str, value: object) -> None:
    now = request.app.state.clock.now()
    async with request.app.state.session_factory() as session, session.begin():
        row = await session.get(PlatformSetting, key)
        if row is None:
            session.add(PlatformSetting(key=key, value=value, created_at=now, updated_at=now))
        else:
            row.value = value
            row.updated_at = now


@router.get("/dashboard")
async def dashboard(request: Request, _admin: Admin = Depends(require_admin)) -> dict[str, object]:
    timezone = ZoneInfo(
        str(await _setting(request, "timezone", request.app.state.settings.app_timezone))
    )
    now = request.app.state.clock.now().astimezone(timezone)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone).astimezone(UTC)
    async with request.app.state.session_factory() as session:
        today_events = await session.scalar(
            select(func.count(Event.id)).where(Event.accepted_at >= day_start)
        )
        delivery_counts = dict(
            (
                await session.execute(
                    select(Delivery.status, func.count(Delivery.id))
                    .where(Delivery.created_at >= day_start)
                    .group_by(Delivery.status)
                )
            ).all()
        )
        failed_plugins = await session.scalar(
            select(func.count(PluginRecord.id)).where(
                PluginRecord.status.in_(["failed", "degraded"])
            )
        )
        errors = (
            await session.scalars(
                select(Delivery)
                .where(Delivery.status == DeliveryStatus.DEAD.value)
                .order_by(Delivery.updated_at.desc())
                .limit(10)
            )
        ).all()
    return {
        "data": {
            "today_events": today_events or 0,
            "succeeded_deliveries": delivery_counts.get(DeliveryStatus.SUCCEEDED.value, 0),
            "failed_deliveries": delivery_counts.get(DeliveryStatus.DEAD.value, 0),
            "retry_wait": delivery_counts.get(DeliveryStatus.RETRY_WAIT.value, 0),
            "failed_plugins": failed_plugins or 0,
            "recent_errors": [
                {
                    "id": item.id,
                    "type": "delivery",
                    "message": item.last_error_message or item.last_error_code or "投递失败",
                    "occurred_at": item.updated_at,
                }
                for item in errors
            ],
        },
        "request_id": request.state.request_id,
    }


@router.get("/settings")
async def get_settings(
    request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    settings = request.app.state.settings
    wecom = {
        "corp_id_configured": bool(settings.wecom_corp_id),
        "agent_id_configured": settings.wecom_agent_id is not None,
        "secret_configured": settings.wecom_secret is not None,
        "callback_token_configured": settings.wecom_callback_token is not None,
        "aes_key_configured": settings.wecom_callback_aes_key is not None,
    }
    wecom["configured"] = all(
        wecom[key] for key in ("corp_id_configured", "agent_id_configured", "secret_configured")
    )
    return {
        "data": {
            "timezone": await _setting(request, "timezone", settings.app_timezone),
            "retention_days": await _setting(
                request, "retention_days", settings.media_retention_seconds // 86400
            ),
            "version": "0.3.0",
            "wecom": wecom,
        },
        "request_id": request.state.request_id,
    }


@router.patch("/settings")
async def update_settings(
    payload: SettingsUpdate,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    for key, value in payload.model_dump(exclude_unset=True).items():
        await _write_setting(request, key, value)
        if key == "timezone" and isinstance(value, str):
            request.app.state.conversation_service.set_timezone(value)
        elif key == "retention_days" and isinstance(value, int):
            request.app.state.media_service.retention_seconds = value * 86400
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="settings.update",
            request_id=request.state.request_id,
            details={"keys": sorted(payload.model_fields_set)},
        )
    return await get_settings(request, admin)
