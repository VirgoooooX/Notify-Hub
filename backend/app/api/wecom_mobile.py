from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.dependencies import get_session, require_admin
from app.api.errors import AppError
from app.application.audit import add_audit
from app.application.mobile_identity_service import MobileIdentityError, MobileMember
from app.application.mobile_reminder_query_service import MobileReminderNotFound
from app.application.reminder_service import ReminderCreate
from app.application.wecom_menu_service import build_wecom_menu_payload
from app.domain.reminders import AckPolicy, ReminderError, ScheduleType
from app.infrastructure.database.models import Admin
from app.infrastructure.database.reminder_models import (
    Reminder,
)
from app.media.errors import MediaError
from app.media.validation import MediaKind
from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

admin_router = APIRouter(tags=["wecom-menu"])
mobile_router = APIRouter(prefix="/mobile", tags=["wecom-mobile"])


class MobileScheduleInput(BaseModel):
    type: Literal["once", "recurring"] = "once"
    at: datetime
    rrule: str | None = Field(default=None, max_length=500)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)

    @model_validator(mode="after")
    def validate_shape(self) -> MobileScheduleInput:
        if self.type == "once" and self.rrule is not None:
            raise ValueError("once schedule does not accept rrule")
        if self.type == "recurring" and not self.rrule:
            raise ValueError("recurring schedule requires rrule")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid timezone") from exc
        return self


class MobileRepeatInput(BaseModel):
    interval_seconds: int = Field(default=300, ge=300)
    max_attempts: int = Field(default=12, ge=1, le=12)
    stop_at: datetime | None = None


class MobileReminderInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20_000)
    content_type: Literal["text", "image", "article"] = "text"
    media_asset_id: str | None = None
    url: str | None = Field(default=None, max_length=2048)
    schedule: MobileScheduleInput
    require_ack: bool = False
    repeat: MobileRepeatInput | None = None


def _summary(reminder: Reminder) -> dict[str, object]:
    return {
        "id": reminder.id,
        "title": reminder.title,
        "content": reminder.content,
        "content_type": reminder.content_type,
        "media_asset_id": reminder.media_asset_id,
        "url": reminder.url,
        "status": reminder.status,
        "schedule_type": reminder.schedule_type,
        "scheduled_at": reminder.scheduled_at,
        "next_run_at": reminder.next_run_at,
        "timezone": reminder.timezone,
        "require_ack": reminder.require_ack,
        "ack_policy": reminder.ack_policy,
        "repeat_interval_seconds": reminder.repeat_interval_seconds,
        "max_attempts": reminder.max_reminders,
    }


async def require_mobile_member(
    request: Request,
    token: Annotated[str | None, Header(alias="X-Mobile-Token")] = None,
) -> MobileMember:
    if not token:
        raise AppError("mobile_auth_required", "Mobile entry token is required", 401)
    try:
        return cast(
            MobileMember,
            await request.app.state.mobile_identity_service.resolve(token),
        )
    except MobileIdentityError as exc:
        raise AppError("invalid_mobile_token", str(exc), 401) from exc


@admin_router.get("/wecom/menu/payload")
async def wecom_menu_payload(
    request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return {
        "data": {
            "payload": build_wecom_menu_payload(),
            "applied": False,
            "note": "Preview only. Use POST /wecom/menu/publish to apply it.",
        },
        "request_id": request.state.request_id,
    }


@admin_router.post("/wecom/menu/publish")
async def publish_wecom_menu(
    request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    if request.app.state.settings.wecom_agent_id is None:
        raise AppError("wecom_not_configured", "WeCom application is not configured", 409)
    payload = build_wecom_menu_payload()
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="wecom.menu.publish.requested",
            resource_type="wecom_menu",
            resource_id=str(request.app.state.settings.wecom_agent_id),
            request_id=request.state.request_id,
        )
    result = await request.app.state.wecom_client.create_menu(payload)
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="wecom.menu.publish",
            resource_type="wecom_menu",
            resource_id=str(request.app.state.settings.wecom_agent_id),
            details={"success": result.success, "error_code": result.error_code},
            request_id=request.state.request_id,
        )
    if not result.success:
        status_code = 503 if result.retryable else 502
        raise AppError(
            result.error_code or "wecom_menu_publish_failed",
            result.error_message or "WeCom rejected the menu publish",
            status_code,
        )
    return {
        "data": {"payload": payload, "applied": True},
        "request_id": request.state.request_id,
    }


@mobile_router.get("/session")
async def mobile_session(
    request: Request, member: MobileMember = Depends(require_mobile_member)
) -> dict[str, object]:
    return {
        "data": {"person_id": member.person_id, "display_name": member.display_name},
        "request_id": request.state.request_id,
    }


@mobile_router.get("/reminders")
async def mobile_reminders(
    request: Request,
    scope: Literal["active", "awaiting_ack", "today", "all"] = Query(default="active"),
    member: MobileMember = Depends(require_mobile_member),
) -> dict[str, object]:
    items = await request.app.state.mobile_reminder_query_service.list(
        member.person_id,
        scope=scope,
        now=request.app.state.clock.now(),
        timezone=request.app.state.settings.app_timezone,
    )
    return {
        "data": {"items": [_summary(item) for item in items], "scope": scope},
        "request_id": request.state.request_id,
    }


@mobile_router.get("/reminders/{reminder_id}")
async def mobile_reminder_detail(
    reminder_id: str,
    request: Request,
    member: MobileMember = Depends(require_mobile_member),
) -> dict[str, object]:
    try:
        result = await request.app.state.mobile_reminder_query_service.detail(
            reminder_id, member.person_id
        )
    except MobileReminderNotFound as exc:
        raise AppError("reminder_not_found", "Reminder was not found", 404) from exc
    detail = _summary(result.reminder)
    detail["occurrences"] = result.occurrences
    return {"data": detail, "request_id": request.state.request_id}


@mobile_router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_mobile_reminder(
    payload: MobileReminderInput,
    request: Request,
    member: MobileMember = Depends(require_mobile_member),
) -> dict[str, object]:
    repeat = payload.repeat
    try:
        reminder = await request.app.state.reminder_service.create(
            ReminderCreate(
                creator_person_id=member.person_id,
                title=payload.title,
                content=payload.content,
                content_type=payload.content_type,
                media_asset_id=payload.media_asset_id,
                url=payload.url,
                schedule_type=ScheduleType(payload.schedule.type),
                timezone=payload.schedule.timezone,
                recipient_ids=(member.person_id,),
                scheduled_at=payload.schedule.at,
                recurrence_rule=payload.schedule.rrule,
                require_ack=payload.require_ack,
                ack_policy=AckPolicy.ANY,
                repeat_interval_seconds=repeat.interval_seconds if repeat else None,
                max_reminders=repeat.max_attempts if repeat else None,
                stop_at=repeat.stop_at if repeat else None,
            )
        )
    except ReminderError as exc:
        raise AppError("invalid_reminder", str(exc), 422) from exc
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="wecom_member",
            actor_id=member.person_id,
            action="reminder.create",
            resource_type="reminder",
            resource_id=reminder.id,
            request_id=request.state.request_id,
        )
    return {"data": _summary(reminder), "request_id": request.state.request_id}


@mobile_router.post("/media", status_code=status.HTTP_201_CREATED)
async def upload_mobile_media(
    request: Request,
    file: Annotated[UploadFile, File()],
    member: MobileMember = Depends(require_mobile_member),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    service = request.app.state.media_service
    data = await file.read(service.limit_for(MediaKind.IMAGE) + 1)
    try:
        asset = await service.create(
            session,
            data,
            MediaKind.IMAGE,
            source="wecom-mobile-upload",
            created_by=member.person_id,
        )
    except MediaError as exc:
        raise AppError(exc.code, str(exc), 422) from exc
    async with request.app.state.session_factory() as audit_session, audit_session.begin():
        add_audit(
            audit_session,
            request.app.state.clock,
            actor_type="wecom_member",
            actor_id=member.person_id,
            action="media.upload",
            resource_type="media_asset",
            resource_id=asset.id,
            request_id=request.state.request_id,
        )
    return {
        "data": {
            "id": asset.id,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
        },
        "request_id": request.state.request_id,
    }
