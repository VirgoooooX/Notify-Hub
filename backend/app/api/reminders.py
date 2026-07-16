from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.dependencies import require_admin
from app.api.errors import AppError
from app.application.audit import add_audit
from app.application.reminder_service import ReminderCreate, ReminderNotFound, ReminderUpdate
from app.domain.reminder_schedules import (
    CronSchedule,
    IntervalSchedule,
    MisfirePolicy,
    ReminderSchedule,
    ScheduleValidationError,
    preview_occurrences,
)
from app.domain.reminders import AckPolicy, ReminderError, ScheduleType, next_rrule_occurrence
from app.infrastructure.database.models import (
    Admin,
    Delivery,
    Event,
    Notification,
    Person,
    WeComIdentity,
)
from app.infrastructure.database.reminder_models import (
    Reminder,
    ReminderOccurrence,
    ReminderOccurrenceRecipient,
    ReminderRecipient,
)
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

router = APIRouter(tags=["reminders"])


class ScheduleInput(BaseModel):
    type: Literal["once", "interval", "cron", "recurring"]
    at: datetime | None = None
    rrule: str | None = Field(default=None, max_length=500)
    interval_seconds: int | None = Field(default=None, ge=300)
    cron_expression: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    start_at: datetime | None = None
    end_at: datetime | None = None
    misfire_policy: Literal["fire_once", "skip"] = "fire_once"

    @model_validator(mode="after")
    def valid_shape(self) -> ScheduleInput:
        if self.type == "once" and (self.at is None or self.rrule is not None):
            raise ValueError("once schedule requires at and forbids rrule")
        if self.type == "recurring" and not self.rrule:
            raise ValueError("recurring schedule requires rrule")
        if self.type == "interval" and self.interval_seconds is None:
            raise ValueError("interval schedule requires interval_seconds")
        if self.type == "interval" and self.start_at is None:
            raise ValueError("interval schedule requires start_at")
        if self.type == "cron" and not self.cron_expression:
            raise ValueError("cron schedule requires cron_expression")
        return self


class RepeatInput(BaseModel):
    interval_seconds: int = Field(default=300, ge=300)
    max_attempts: int = Field(default=12, ge=1, le=12)
    stop_at: datetime | None = None


class ReminderInput(BaseModel):
    creator_person_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20_000)
    content_type: Literal["text", "image", "article"] = "text"
    media_asset_id: str | None = None
    url: str | None = None
    schedule: ScheduleInput
    recipients: list[str] = Field(default_factory=list, max_length=100)
    broadcast: bool = False
    notify_on_all_completed: bool = False
    require_ack: bool = False
    ack_policy: Literal["any", "all", "each"] = "any"
    repeat: RepeatInput | None = None

    @model_validator(mode="after")
    def valid_audience(self) -> ReminderInput:
        if self.broadcast and self.recipients:
            raise ValueError("broadcast reminders must not include explicit recipients")
        if not self.broadcast and not self.recipients:
            raise ValueError("at least one explicit recipient is required")
        if self.notify_on_all_completed and not self.broadcast:
            raise ValueError("all-completed notifications require a broadcast reminder")
        if self.notify_on_all_completed and not self.require_ack:
            raise ValueError("all-completed notifications require an interactive reminder")
        if self.notify_on_all_completed and self.ack_policy != "all":
            raise ValueError("all-completed notifications require ack_policy=all")
        return self


class SnoozeInput(BaseModel):
    until: datetime


class CleanupInput(BaseModel):
    before: datetime
    dry_run: bool = True
    limit: int = Field(default=1000, ge=1, le=10_000)


class ReminderPatchInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=20_000)
    content_type: Literal["text", "image", "article"] | None = None
    media_asset_id: str | None = None
    url: str | None = None
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
        "content_type": item.content_type,
        "media_asset_id": item.media_asset_id,
        "url": item.url,
        "schedule_type": item.schedule_type,
        "scheduled_at": item.scheduled_at,
        "recurrence_rule": item.recurrence_rule,
        "schedule_config": item.schedule_config,
        "timezone": item.timezone,
        "start_at": item.start_at,
        "end_at": item.end_at,
        "misfire_policy": item.misfire_policy,
        "next_run_at": item.next_run_at,
        "status": "awaiting_ack"
        if item.status == "active" and item.require_ack and item.reminder_count
        else item.status,
        "persisted_status": item.status,
        "broadcast": item.broadcast,
        "notify_on_all_completed": item.notify_on_all_completed,
        "require_ack": item.require_ack,
        "ack_policy": item.ack_policy,
        "repeat_interval_seconds": item.repeat_interval_seconds,
        "max_reminders": item.max_reminders,
        "max_attempts": item.max_reminders,
        "reminder_count": item.reminder_count,
        "attempt_count": item.reminder_count,
        "stop_at": item.stop_at,
        "escalation_stop_after_seconds": item.escalation_stop_after_seconds,
        "interaction_mode": (
            "latest_menu"
            if item.require_ack and item.repeat_interval_seconds is not None
            else "none"
        ),
    }


def _service_error(exc: ReminderError) -> AppError:
    if isinstance(exc, ReminderNotFound):
        return AppError("not_found", "Reminder not found", 404)
    if "cannot" in str(exc):
        return AppError("invalid_state", str(exc), 409)
    return AppError("invalid_reminder", str(exc), 422)


async def _audit_reminder(
    request: Request,
    admin: Admin,
    *,
    action: str,
    reminder_id: str,
    details: dict[str, object] | None = None,
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action=action,
            resource_type="reminder",
            resource_id=reminder_id,
            request_id=request.state.request_id,
            details=details or {},
        )


@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_reminder(
    payload: ReminderInput, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    repeat = payload.repeat
    recipient_ids = list(payload.recipients)
    if payload.broadcast:
        async with request.app.state.session_factory() as session:
            recipient_ids = list(
                await session.scalars(
                    select(Person.id)
                    .join(WeComIdentity, WeComIdentity.person_id == Person.id)
                    .where(Person.active.is_(True), WeComIdentity.active.is_(True))
                    .distinct()
                    .order_by(Person.id)
                )
            )
        if not recipient_ids:
            raise AppError(
                "broadcast_audience_empty",
                "No active Notify Hub members with a WeCom identity are available",
                422,
            )
    try:
        item = await request.app.state.reminder_service.create(
            ReminderCreate(
                creator_person_id=payload.creator_person_id or recipient_ids[0],
                title=payload.title,
                content=payload.content,
                content_type=payload.content_type,
                media_asset_id=payload.media_asset_id,
                url=payload.url,
                schedule_type=ScheduleType(payload.schedule.type),
                timezone=payload.schedule.timezone,
                recipient_ids=tuple(recipient_ids),
                broadcast=payload.broadcast,
                notify_on_all_completed=payload.notify_on_all_completed,
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
            )
        )
    except ReminderError as exc:
        raise _service_error(exc) from exc
    await _audit_reminder(
        request,
        admin,
        action="reminder.create",
        reminder_id=item.id,
        details={
            "broadcast": payload.broadcast,
            "notify_on_all_completed": payload.notify_on_all_completed,
            "audience_count": len(recipient_ids),
        },
    )
    return {"data": _view(item), "request_id": request.state.request_id}


class PreviewInput(BaseModel):
    type: Literal["interval", "cron", "recurring"] = "recurring"
    rrule: str | None = Field(default=None, max_length=500)
    interval_seconds: int | None = Field(default=None, ge=300)
    cron_expression: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    start_at: datetime | None = None
    end_at: datetime | None = None
    count: int = Field(default=5, ge=1, le=50)


@router.post("/reminders/preview")
async def preview_rrule(
    payload: PreviewInput, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    start = payload.start_at or request.app.state.clock.now()
    timezone = payload.timezone
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise AppError("invalid_timezone", "Invalid timezone", 422) from exc

    triggers: list[datetime] = []
    if payload.type == "recurring":
        if not payload.rrule:
            raise AppError("invalid_schedule", "RRULE is required", 422)
        current = start
        for _ in range(payload.count):
            nxt = next_rrule_occurrence(
                payload.rrule,
                timezone=timezone,
                after=current,
                dtstart=start,
            )
            if nxt is None:
                break
            triggers.append(nxt)
            current = nxt
    else:
        try:
            if payload.type == "interval":
                if payload.interval_seconds is None:
                    raise ScheduleValidationError("interval_seconds is required")
                schedule: ReminderSchedule = IntervalSchedule(
                    payload.interval_seconds, start, timezone
                )
            else:
                if not payload.cron_expression:
                    raise ScheduleValidationError("cron_expression is required")
                schedule = CronSchedule(payload.cron_expression, timezone)
            triggers = list(
                preview_occurrences(
                    schedule,
                    after=start,
                    count=payload.count,
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                )
            )
        except ScheduleValidationError as exc:
            raise AppError("invalid_schedule", str(exc), 422) from exc

    return {
        "data": {"triggers": [t.isoformat() for t in triggers]},
        "request_id": request.state.request_id,
    }


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


@router.get("/reminders/metrics")
async def reminder_metrics(
    request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    data = await request.app.state.reminder_maintenance_service.metrics()
    return {"data": data, "request_id": request.state.request_id}


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

        occurrences = (
            await session.scalars(
                select(ReminderOccurrence)
                .where(ReminderOccurrence.reminder_id == reminder_id)
                .order_by(ReminderOccurrence.triggered_at.desc())
            )
        ).all()

        occurrence_recipients: dict[str, list[dict[str, object]]] = {}
        latest_targets: dict[tuple[str, str], list[str]] = {}
        if occurrences:
            occ_ids = [occ.id for occ in occurrences]
            identity_rows = (
                await session.execute(
                    select(
                        WeComIdentity.latest_interactive_occurrence_id,
                        WeComIdentity.person_id,
                        WeComIdentity.user_id,
                    ).where(
                        WeComIdentity.latest_interactive_occurrence_id.in_(occ_ids),
                        WeComIdentity.active.is_(True),
                    )
                )
            ).all()
            for occurrence_id, person_id, user_id in identity_rows:
                if occurrence_id is not None:
                    latest_targets.setdefault((occurrence_id, person_id), []).append(user_id)
            orc_rows = (
                await session.execute(
                    select(ReminderOccurrenceRecipient, Person.display_name)
                    .join(Person, Person.id == ReminderOccurrenceRecipient.person_id)
                    .where(ReminderOccurrenceRecipient.occurrence_id.in_(occ_ids))
                )
            ).all()
            for orc, display_name in orc_rows:
                occurrence_recipients.setdefault(orc.occurrence_id, []).append(
                    {
                        "id": orc.id,
                        "person_id": orc.person_id,
                        "name": display_name,
                        "status": orc.status,
                        "notify_count": orc.notify_count,
                        "next_notify_at": orc.next_notify_at.isoformat()
                        if orc.next_notify_at
                        else None,
                        "last_notified_at": orc.last_notified_at.isoformat()
                        if orc.last_notified_at
                        else None,
                        "acknowledged_at": orc.acknowledged_at.isoformat()
                        if orc.acknowledged_at
                        else None,
                        "acknowledged_by": orc.acknowledged_by,
                        "latest_interactive_user_ids": latest_targets.get(
                            (orc.occurrence_id, orc.person_id), []
                        ),
                    }
                )

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
    data["occurrences"] = [
        {
            "id": occ.id,
            "occurrence_key": occ.occurrence_key,
            "scheduled_for": occ.scheduled_for,
            "triggered_at": occ.triggered_at,
            "status": occ.status,
            "title": occ.title_snapshot,
            "content": occ.content_snapshot,
            "content_type": occ.content_type_snapshot,
            "media_asset_id": occ.media_asset_id_snapshot,
            "completed_at": occ.completed_at,
            "completed_by": occ.completed_by,
            "expires_at": occ.expires_at,
            "recipients": occurrence_recipients.get(occ.id, []),
        }
        for occ in occurrences
    ]
    return {"data": data, "request_id": request.state.request_id}


@router.patch("/reminders/{reminder_id}")
async def update_reminder(
    reminder_id: str,
    payload: ReminderPatchInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    schedule, repeat = payload.schedule, payload.repeat
    try:
        item = await request.app.state.reminder_service.update(
            reminder_id,
            ReminderUpdate(
                title=payload.title,
                content=payload.content,
                content_type=payload.content_type,
                media_asset_id=payload.media_asset_id,
                url=payload.url,
                schedule_type=ScheduleType(schedule.type) if schedule else None,
                timezone=schedule.timezone if schedule else None,
                scheduled_at=schedule.at if schedule else None,
                recurrence_rule=schedule.rrule if schedule else None,
                interval_seconds=schedule.interval_seconds if schedule else None,
                cron_expression=schedule.cron_expression if schedule else None,
                start_at=schedule.start_at if schedule else None,
                end_at=schedule.end_at if schedule else None,
                misfire_policy=MisfirePolicy(schedule.misfire_policy) if schedule else None,
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
    await _audit_reminder(
        request,
        admin,
        action="reminder.update",
        reminder_id=item.id,
        details={"fields": sorted(payload.model_fields_set)},
    )
    return {"data": _view(item), "request_id": request.state.request_id}


async def _transition(
    reminder_id: str, operation: str, request: Request, admin: Admin
) -> dict[str, object]:
    try:
        item = await getattr(request.app.state.reminder_service, operation)(reminder_id)
    except ReminderError as exc:
        raise _service_error(exc) from exc
    await _audit_reminder(
        request,
        admin,
        action=f"reminder.{operation}",
        reminder_id=item.id,
    )
    return {"data": _view(item), "request_id": request.state.request_id}


@router.post("/reminders/{reminder_id}/pause")
async def pause_reminder(
    reminder_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "pause", request, admin)


@router.post("/reminders/{reminder_id}/resume")
async def resume_reminder(
    reminder_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "resume", request, admin)


@router.post("/reminders/{reminder_id}/complete")
async def complete_reminder(
    reminder_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "complete", request, admin)


@router.post("/reminders/{reminder_id}/cancel")
async def cancel_reminder(
    reminder_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    return await _transition(reminder_id, "cancel", request, admin)


@router.post("/reminders/{reminder_id}/snooze")
async def snooze_reminder(
    reminder_id: str,
    payload: SnoozeInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        item = await request.app.state.reminder_service.snooze(reminder_id, until=payload.until)
    except ReminderError as exc:
        raise _service_error(exc) from exc
    await _audit_reminder(
        request,
        admin,
        action="reminder.snooze",
        reminder_id=item.id,
        details={"until": payload.until.isoformat()},
    )
    return {"data": _view(item), "request_id": request.state.request_id}


@router.post("/reminders/interactions/{event_id}/retry")
async def retry_interaction(
    event_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    retried = await request.app.state.reminder_maintenance_service.retry_interaction(event_id)
    if not retried:
        raise AppError("not_retryable", "Interaction is not dead or does not exist", 409)
    await _audit_reminder(
        request,
        admin,
        action="reminder.interaction.retry",
        reminder_id=event_id,
    )
    return {"data": {"retried": True}, "request_id": request.state.request_id}


@router.post("/reminders/messages/{message_id}/retry")
async def retry_incoming_message(
    message_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    retried = await request.app.state.reminder_maintenance_service.retry_message(message_id)
    if not retried:
        raise AppError("not_retryable", "Message is not failed or does not exist", 409)
    await _audit_reminder(
        request,
        admin,
        action="reminder.message.retry",
        reminder_id=message_id,
    )
    return {"data": {"retried": True}, "request_id": request.state.request_id}


@router.post("/reminders/maintenance/cleanup")
async def cleanup_reminder_history(
    payload: CleanupInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    result = await request.app.state.reminder_maintenance_service.cleanup(
        before=payload.before,
        dry_run=payload.dry_run,
        limit=payload.limit,
    )
    await _audit_reminder(
        request,
        admin,
        action="reminder.maintenance.cleanup",
        reminder_id="history",
        details={
            "before": payload.before.isoformat(),
            "dry_run": payload.dry_run,
            "interaction_events": result.interaction_events,
            "incoming_messages": result.incoming_messages,
            "reminder_drafts": result.reminder_drafts,
        },
    )
    return {
        "data": {
            "interaction_events": result.interaction_events,
            "incoming_messages": result.incoming_messages,
            "reminder_drafts": result.reminder_drafts,
            "dry_run": result.dry_run,
        },
        "request_id": request.state.request_id,
    }
