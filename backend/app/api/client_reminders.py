from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.api.dependencies import require_api_client
from app.api.errors import AppError
from app.application.reminder_access import (
    ReminderAccessDenied,
    ReminderActor,
    ReminderIdempotencyConflict,
    ReminderPermissions,
    ReminderQuotaExceeded,
)
from app.application.reminder_service import ReminderCreate
from app.domain.reminder_schedules import MisfirePolicy
from app.domain.reminders import AckPolicy, ReminderError, ScheduleType
from app.infrastructure.database.models import ApiClient
from app.infrastructure.database.reminder_models import Reminder
from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, Field, model_validator

router = APIRouter(tags=["client-reminders"])


class ClientReminderSchedule(BaseModel):
    type: Literal["once", "interval", "cron", "recurring"]
    at: datetime | None = None
    rrule: str | None = Field(default=None, max_length=500)
    interval_seconds: int | None = Field(default=None, ge=300)
    cron_expression: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    start_at: datetime | None = None
    end_at: datetime | None = None
    misfire_policy: Literal["fire_once", "skip"] = "fire_once"
    mode: Literal["once", "interval", "cron", "recurring"] | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> ClientReminderSchedule:
        if self.type == "once" and (self.at is None or self.rrule is not None):
            raise ValueError("once schedule requires at and forbids rrule")
        if self.type == "recurring" and not self.rrule:
            raise ValueError("recurring schedule requires rrule")
        if self.type == "interval" and (self.interval_seconds is None or self.start_at is None):
            raise ValueError("interval schedule requires interval_seconds and start_at")
        if self.type == "cron" and not self.cron_expression:
            raise ValueError("cron schedule requires cron_expression")
        if self.type == "once" and self.mode not in {None, "once"}:
            raise ValueError("once schedule mode must be once")
        if self.type == "recurring" and self.mode == "once":
            raise ValueError("recurring schedule cannot use once mode")
        return self


class ClientReminderRepeat(BaseModel):
    interval_seconds: int = Field(default=300, ge=300)
    max_attempts: int = Field(default=12, ge=1, le=12)
    stop_at: datetime | None = None


class ClientReminderCreate(BaseModel):
    creator_person_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20_000)
    content_type: Literal["text", "image", "article"] = "text"
    media_asset_id: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2048)
    schedule: ClientReminderSchedule
    recipients: list[str] = Field(min_length=1, max_length=100)
    require_ack: bool = False
    ack_policy: Literal["any", "all", "each"] = "any"
    repeat: ClientReminderRepeat | None = None


def _view(reminder: Reminder) -> dict[str, object]:
    return {
        "id": reminder.id,
        "title": reminder.title,
        "status": reminder.status,
        "schedule_type": reminder.schedule_type,
        "next_run_at": reminder.next_run_at,
        "timezone": reminder.timezone,
        "require_ack": reminder.require_ack,
    }


@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_client_reminder(
    payload: ClientReminderCreate,
    request: Request,
    client: ApiClient = Depends(require_api_client),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    repeat = payload.repeat
    creator_person_id = payload.creator_person_id or payload.recipients[0]
    try:
        result = await request.app.state.reminder_access_service.create(
            ReminderCreate(
                creator_person_id=creator_person_id,
                title=payload.title,
                content=payload.content,
                content_type=payload.content_type,
                media_asset_id=payload.media_asset_id,
                url=payload.url,
                schedule_type=ScheduleType(payload.schedule.type),
                timezone=payload.schedule.timezone,
                recipient_ids=tuple(payload.recipients),
                scheduled_at=payload.schedule.at,
                recurrence_rule=payload.schedule.rrule,
                interval_seconds=payload.schedule.interval_seconds,
                cron_expression=payload.schedule.cron_expression,
                start_at=payload.schedule.start_at,
                end_at=payload.schedule.end_at,
                misfire_policy=MisfirePolicy(payload.schedule.misfire_policy),
                require_ack=payload.require_ack,
                ack_policy=AckPolicy(payload.ack_policy),
                repeat_interval_seconds=repeat.interval_seconds if repeat else None,
                max_reminders=repeat.max_attempts if repeat else None,
                stop_at=repeat.stop_at if repeat else None,
            ),
            actor=ReminderActor("api_client", client.id),
            permissions=ReminderPermissions(
                allow_create=client.allow_reminders,
                allow_recurring=client.allow_recurring,
                allow_cron=client.allow_cron,
                allow_interactive=client.allow_interactive,
                allow_media=client.allow_media,
                allowed_recipients=tuple(client.allowed_recipient_ids),
                max_active=client.max_active_reminders,
            ),
            schedule_mode=payload.schedule.mode or payload.schedule.type,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except ReminderAccessDenied as exc:
        raise AppError("reminder_forbidden", str(exc), 403) from exc
    except ReminderQuotaExceeded as exc:
        raise AppError("reminder_quota_exceeded", str(exc), 409) from exc
    except ReminderIdempotencyConflict as exc:
        raise AppError("idempotency_conflict", str(exc), 409) from exc
    except ReminderError as exc:
        raise AppError("invalid_reminder", str(exc), 422) from exc
    data = _view(result.reminder)
    data["duplicate"] = result.duplicate
    return {"data": data, "request_id": request.state.request_id}
