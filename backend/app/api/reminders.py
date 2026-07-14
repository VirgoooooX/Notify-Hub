from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.api.dependencies import require_admin
from app.api.errors import AppError
from app.application.reminder_service import ReminderCreate, ReminderNotFound, ReminderUpdate
from app.domain.reminders import AckPolicy, ReminderError, ScheduleType
from app.infrastructure.database.models import Admin, Delivery, Event, Notification, Person
from app.infrastructure.database.reminder_models import Reminder, ReminderRecipient
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

router = APIRouter(tags=["reminders"])


class ScheduleInput(BaseModel):
    type: Literal["once", "recurring"]
    at: datetime | None = None
    rrule: str | None = Field(default=None, max_length=500)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)

    @model_validator(mode="after")
    def valid_shape(self) -> ScheduleInput:
        if self.type == "once" and (self.at is None or self.rrule is not None):
            raise ValueError("once schedule requires at and forbids rrule")
        if self.type == "recurring" and not self.rrule:
            raise ValueError("recurring schedule requires rrule")
        return self


class RepeatInput(BaseModel):
    interval_seconds: int = Field(default=300, ge=300)
    max_attempts: int = Field(default=12, ge=1, le=12)
    stop_at: datetime | None = None


class ReminderInput(BaseModel):
    creator_person_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20_000)
    schedule: ScheduleInput
    recipients: list[str] = Field(min_length=1, max_length=100)
    require_ack: bool = False
    ack_policy: Literal["any", "all", "each"] = "any"
    repeat: RepeatInput | None = None


class SnoozeInput(BaseModel):
    until: datetime


class ReminderPatchInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=20_000)
    schedule: ScheduleInput | None = None
    recipients: list[str] | None = Field(default=None, min_length=1, max_length=100)
    require_ack: bool | None = None
    ack_policy: Literal["any", "all", "each"] | None = None
    repeat: RepeatInput | None = None


def _view(reminder: Reminder) -> dict[str, object]:
    item = reminder
    return {
        "id": item.id,
        "creator_person_id": item.creator_person_id,
        "title": item.title,
        "content": item.content,
        "schedule_type": item.schedule_type,
        "scheduled_at": item.scheduled_at,
        "recurrence_rule": item.recurrence_rule,
        "timezone": item.timezone,
        "next_run_at": item.next_run_at,
        "status": "awaiting_ack"
        if item.status == "active" and item.require_ack and item.reminder_count
        else item.status,
        "persisted_status": item.status,
        "require_ack": item.require_ack,
        "ack_policy": item.ack_policy,
        "repeat_interval_seconds": item.repeat_interval_seconds,
        "max_reminders": item.max_reminders,
        "max_attempts": item.max_reminders,
        "reminder_count": item.reminder_count,
        "attempt_count": item.reminder_count,
        "stop_at": item.stop_at,
    }


def _service_error(exc: ReminderError) -> AppError:
    if isinstance(exc, ReminderNotFound):
        return AppError("not_found", "Reminder not found", 404)
    if "cannot" in str(exc):
        return AppError("invalid_state", str(exc), 409)
    return AppError("invalid_reminder", str(exc), 422)


@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_reminder(
    payload: ReminderInput, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    repeat = payload.repeat
    try:
        item = await request.app.state.reminder_service.create(
            ReminderCreate(
                creator_person_id=payload.creator_person_id or payload.recipients[0],
                title=payload.title,
                content=payload.content,
                schedule_type=ScheduleType(payload.schedule.type),
                timezone=payload.schedule.timezone,
                recipient_ids=tuple(payload.recipients),
                scheduled_at=payload.schedule.at,
                recurrence_rule=payload.schedule.rrule,
                require_ack=payload.require_ack,
                ack_policy=AckPolicy(payload.ack_policy),
                repeat_interval_seconds=repeat.interval_seconds if repeat else None,
                max_reminders=repeat.max_attempts if repeat else None,
                stop_at=repeat.stop_at if repeat else None,
            )
        )
    except ReminderError as exc:
        raise _service_error(exc) from exc
    return {"data": _view(item), "request_id": request.state.request_id}


@router.get("/reminders")
async def list_reminders(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    status_filter: str | None = Query(default=None, alias="status"),
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    page, page_size = max(page, 1), min(max(page_size, 1), 200)
    items = await request.app.state.reminder_service.list(
        offset=(page - 1) * page_size,
        limit=page_size,
        status=status_filter,
    )
    total = await request.app.state.reminder_service.count(status=status_filter)
    return {
        "data": {
            "items": [_view(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
        "request_id": request.state.request_id,
    }


@router.get("/reminders/{reminder_id}")
async def get_reminder(
    reminder_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    try:
        item = await request.app.state.reminder_service.get(reminder_id)
    except ReminderError as exc:
        raise _service_error(exc) from exc
    async with request.app.state.session_factory() as session:
        recipient_rows = (
            await session.execute(
                select(ReminderRecipient, Person.display_name)
                .join(Person, Person.id == ReminderRecipient.person_id)
                .where(ReminderRecipient.reminder_id == reminder_id)
            )
        ).all()
        delivery_rows = (
            await session.execute(
                select(Delivery, Event.accepted_at)
                .join(Notification, Notification.id == Delivery.notification_id)
                .join(Event, Event.id == Notification.event_id)
                .where(Event.source_type == "reminder", Event.source_id == reminder_id)
                .order_by(Event.accepted_at, Delivery.created_at)
            )
        ).all()
    data = _view(item)
    data["recipients"] = [
        {
            "id": recipient.person_id,
            "name": display_name,
            "status": recipient.status,
            "acknowledged_at": recipient.acknowledged_at,
            "attempt_count": recipient.notify_count,
        }
        for recipient, display_name in recipient_rows
    ]
    data["timeline"] = [
        {
            "id": delivery.id,
            "type": "delivery",
            "message": f"{delivery.recipient_id}: {delivery.status}",
            "occurred_at": delivery.updated_at or accepted_at,
        }
        for delivery, accepted_at in delivery_rows
    ]
    return {"data": data, "request_id": request.state.request_id}


@router.patch("/reminders/{reminder_id}")
async def update_reminder(
    reminder_id: str,
    payload: ReminderPatchInput,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    schedule, repeat = payload.schedule, payload.repeat
    try:
        item = await request.app.state.reminder_service.update(
            reminder_id,
            ReminderUpdate(
                title=payload.title,
                content=payload.content,
                schedule_type=ScheduleType(schedule.type) if schedule else None,
                timezone=schedule.timezone if schedule else None,
                scheduled_at=schedule.at if schedule else None,
                recurrence_rule=schedule.rrule if schedule else None,
                recipient_ids=tuple(payload.recipients) if payload.recipients else None,
                require_ack=payload.require_ack,
                ack_policy=AckPolicy(payload.ack_policy) if payload.ack_policy else None,
                repeat_interval_seconds=repeat.interval_seconds if repeat else None,
                max_reminders=repeat.max_attempts if repeat else None,
                stop_at=repeat.stop_at if repeat else None,
            ),
        )
    except ReminderError as exc:
        raise _service_error(exc) from exc
    return {"data": _view(item), "request_id": request.state.request_id}


async def _transition(reminder_id: str, operation: str, request: Request) -> dict[str, object]:
    try:
        item = await getattr(request.app.state.reminder_service, operation)(reminder_id)
    except ReminderError as exc:
        raise _service_error(exc) from exc
    return {"data": _view(item), "request_id": request.state.request_id}


@router.post("/reminders/{reminder_id}/pause")
async def pause_reminder(
    reminder_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "pause", request)


@router.post("/reminders/{reminder_id}/resume")
async def resume_reminder(
    reminder_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "resume", request)


@router.post("/reminders/{reminder_id}/complete")
async def complete_reminder(
    reminder_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "complete", request)


@router.post("/reminders/{reminder_id}/cancel")
async def cancel_reminder(
    reminder_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "cancel", request)


@router.post("/reminders/{reminder_id}/snooze")
async def snooze_reminder(
    reminder_id: str,
    payload: SnoozeInput,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        item = await request.app.state.reminder_service.snooze(reminder_id, until=payload.until)
    except ReminderError as exc:
        raise _service_error(exc) from exc
    return {"data": _view(item), "request_id": request.state.request_id}
