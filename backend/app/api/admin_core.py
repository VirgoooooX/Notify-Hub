from app.api.dependencies import require_admin
from app.api.errors import AppError
from app.api.schemas import (
    ApiClientCreate,
    ApiClientUpdate,
    IdentityCreate,
    NotificationCreate,
    PersonCreate,
    PersonUpdate,
    WeComTestRequest,
)
from app.application.audit import add_audit
from app.application.notification_service import NotificationDraft
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import (
    Admin,
    ApiClient,
    Delivery,
    DeliveryAttempt,
    DeliveryStatus,
    Event,
    Notification,
    Person,
    WeComIdentity,
)
from app.infrastructure.security.tokens import create_api_key, hash_token
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["admin"])


@router.post("/notifications", status_code=202)
async def create_notification(
    payload: NotificationCreate, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    notification_id = await request.app.state.notification_service.create(
        NotificationDraft(
            title=payload.title,
            content=payload.content,
            message_type=payload.message_type,
            recipients=payload.recipients,
            priority=payload.priority,
            url=str(payload.url) if payload.url else None,
            image_url=str(payload.image_url) if payload.image_url else None,
            media_asset_id=payload.media_asset_id,
            require_ack=payload.require_ack,
        )
    )
    return {
        "data": {"notification_id": notification_id, "status": "accepted"},
        "request_id": request.state.request_id,
    }


@router.post("/channels/wecom/test", status_code=202)
async def test_wecom_channel(
    payload: WeComTestRequest, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    notification_id = await request.app.state.notification_service.create(
        NotificationDraft(
            title="Notify Hub 测试",
            content="事件、队列与企业微信投递链路测试",
            message_type=payload.message_type,
            recipients=[payload.recipient_id],
            event_type="system.channel_test",
        )
    )
    return {
        "data": {"notification_id": notification_id, "status": "accepted"},
        "request_id": request.state.request_id,
    }


def client_view(client: ApiClient) -> dict[str, object]:
    return {
        "id": client.id,
        "name": client.name,
        "key_prefix": client.key_prefix,
        "allowed_event_types": client.allowed_event_types,
        "allowed_recipient_ids": client.allowed_recipient_ids,
        "allow_broadcast": client.allow_broadcast,
        "allow_critical": client.allow_critical,
        "allow_media": client.allow_media,
        "allow_voice": client.allow_voice,
        "rate_limit_per_minute": client.rate_limit_per_minute,
        "revoked_at": client.revoked_at,
        "last_used_at": client.last_used_at,
        "status": "revoked" if client.revoked_at else "active",
    }


@router.post("/api-clients", status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ApiClientCreate, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    now = request.app.state.clock.now()
    key = create_api_key()
    client = ApiClient(
        id=payload.id or new_id("client"),
        name=payload.name,
        key_prefix=key[:12],
        key_hash=hash_token(key),
        allowed_event_types=payload.allowed_event_types,
        allowed_recipient_ids=payload.allowed_recipient_ids,
        allow_broadcast=payload.allow_broadcast,
        allow_critical=payload.allow_critical,
        allow_media=payload.allow_media,
        allow_voice=payload.allow_voice,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        revoked_at=None,
        last_used_at=None,
        created_at=now,
        updated_at=now,
    )
    async with request.app.state.session_factory() as session, session.begin():
        session.add(client)
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="api_client.create",
            resource_type="api_client",
            resource_id=client.id,
            request_id=request.state.request_id,
        )
    data = client_view(client)
    data["api_key"] = key
    return {"data": data, "request_id": request.state.request_id}


@router.get("/api-clients")
async def list_clients(
    request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        clients = (
            await session.scalars(select(ApiClient).order_by(ApiClient.created_at.desc()))
        ).all()
    return {"data": [client_view(item) for item in clients], "request_id": request.state.request_id}


@router.patch("/api-clients/{client_id}")
async def update_client(
    client_id: str,
    payload: ApiClientUpdate,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    async with request.app.state.session_factory() as session, session.begin():
        client = await session.get(ApiClient, client_id)
        if client is None:
            raise AppError("not_found", "API client not found", 404)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(client, key, value)
        client.updated_at = request.app.state.clock.now()
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="api_client.update",
            resource_type="api_client",
            resource_id=client.id,
            request_id=request.state.request_id,
        )
    return {"data": client_view(client), "request_id": request.state.request_id}


@router.post("/api-clients/{client_id}/rotate-key")
async def rotate_client_key(
    client_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    key = create_api_key()
    async with request.app.state.session_factory() as session, session.begin():
        client = await session.get(ApiClient, client_id)
        if client is None:
            raise AppError("not_found", "API client not found", 404)
        client.key_prefix, client.key_hash = key[:12], hash_token(key)
        client.updated_at = request.app.state.clock.now()
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="api_client.rotate",
            resource_type="api_client",
            resource_id=client.id,
            request_id=request.state.request_id,
        )
    data = client_view(client)
    data["api_key"] = key
    return {"data": data, "request_id": request.state.request_id}


@router.post("/api-clients/{client_id}/revoke", status_code=204)
async def revoke_client(
    client_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        client = await session.get(ApiClient, client_id)
        if client is None:
            raise AppError("not_found", "API client not found", 404)
        client.revoked_at = request.app.state.clock.now()
        client.updated_at = client.revoked_at
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="api_client.revoke",
            resource_type="api_client",
            resource_id=client.id,
            request_id=request.state.request_id,
        )


@router.get("/events")
async def list_events(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    source_id: str | None = None,
    event_type: str | None = None,
    event_status: str | None = None,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    query = select(Event)
    count_query = select(func.count(Event.id))
    for criterion in (
        Event.source_id == source_id if source_id else None,
        Event.event_type == event_type if event_type else None,
        Event.status == event_status if event_status else None,
    ):
        if criterion is not None:
            query, count_query = query.where(criterion), count_query.where(criterion)
    async with request.app.state.session_factory() as session:
        total = await session.scalar(count_query)
        events = (
            await session.scalars(
                query.order_by(Event.accepted_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    return {
        "data": {
            "items": [
                {
                    "id": e.id,
                    "source_type": e.source_type,
                    "source_id": e.source_id,
                    "event_type": e.event_type,
                    "event_key": e.event_key,
                    "title": e.title,
                    "level": e.level,
                    "status": e.status,
                    "accepted_at": e.accepted_at,
                }
                for e in events
            ],
            "page": page,
            "page_size": page_size,
            "total": total or 0,
        },
        "request_id": request.state.request_id,
    }


@router.get("/notifications")
async def list_notifications(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = Query(default=None, alias="status"),
    keyword: str | None = None,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    query = select(Notification).options(selectinload(Notification.deliveries))
    count_query = select(func.count(Notification.id))
    if status_filter:
        criterion = Notification.deliveries.any(Delivery.status == status_filter)
        query, count_query = query.where(criterion), count_query.where(criterion)
    if keyword:
        pattern = f"%{keyword}%"
        criterion = (
            Notification.title.ilike(pattern)
            | Notification.content.ilike(pattern)
            | Notification.event_id.ilike(pattern)
        )
        query, count_query = query.where(criterion), count_query.where(criterion)
    async with request.app.state.session_factory() as session:
        total = await session.scalar(count_query)
        rows = (
            await session.scalars(
                query.order_by(Notification.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()

    def aggregate_status(notification: Notification) -> str:
        statuses = {delivery.status for delivery in notification.deliveries}
        for value in ("dead", "retry_wait", "processing", "pending", "succeeded", "cancelled"):
            if value in statuses:
                return value
        return "pending"

    return {
        "data": {
            "items": [
                {
                    "id": n.id,
                    "event_id": n.event_id,
                    "message_type": n.message_type,
                    "title": n.title,
                    "content": n.content,
                    "priority": n.priority,
                    "status": aggregate_status(n),
                    "created_at": n.created_at,
                }
                for n in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total or 0,
        },
        "request_id": request.state.request_id,
    }


@router.get("/notifications/{notification_id}")
async def get_notification(
    notification_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        notification = await session.scalar(
            select(Notification)
            .where(Notification.id == notification_id)
            .options(selectinload(Notification.deliveries), selectinload(Notification.event))
        )
        if notification is None:
            raise AppError("not_found", "Notification not found", 404)
        data = {
            "id": notification.id,
            "event_id": notification.event_id,
            "title": notification.title,
            "content": notification.content,
            "message_type": notification.message_type,
            "priority": notification.priority,
            "created_at": notification.created_at,
            "event": None
            if notification.event is None
            else {
                "id": notification.event.id,
                "event_type": notification.event.event_type,
                "event_key": notification.event.event_key,
                "source_type": notification.event.source_type,
                "source_id": notification.event.source_id,
            },
            "deliveries": [
                {
                    "id": d.id,
                    "recipient_id": d.recipient_id,
                    "status": d.status,
                    "attempt_count": d.attempt_count,
                    "last_error_code": d.last_error_code,
                    "last_error_message": d.last_error_message,
                }
                for d in notification.deliveries
            ],
        }
    return {"data": data, "request_id": request.state.request_id}


@router.get("/deliveries/{delivery_id}/attempts")
async def delivery_attempts(
    delivery_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        if await session.get(Delivery, delivery_id) is None:
            raise AppError("not_found", "Delivery not found", 404)
        rows = (
            await session.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.delivery_id == delivery_id)
                .order_by(DeliveryAttempt.attempt_no)
            )
        ).all()
    return {
        "data": [
            {
                "id": a.id,
                "attempt_no": a.attempt_no,
                "status": a.status,
                "started_at": a.started_at,
                "finished_at": a.finished_at,
                "error_code": a.error_code,
                "error_message": a.error_message,
                "provider_status": a.provider_status,
            }
            for a in rows
        ],
        "request_id": request.state.request_id,
    }


@router.post("/deliveries/{delivery_id}/retry", status_code=204)
async def retry_delivery(
    delivery_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        delivery = await session.get(Delivery, delivery_id)
        if delivery is None:
            raise AppError("not_found", "Delivery not found", 404)
        if delivery.status != DeliveryStatus.DEAD.value:
            raise AppError("invalid_state", "Only dead deliveries can be retried", 409)
        delivery.status = DeliveryStatus.PENDING.value
        delivery.next_attempt_at = request.app.state.clock.now()
        delivery.claimed_by = None
        delivery.claim_expires_at = None
        delivery.updated_at = delivery.next_attempt_at
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="delivery.retry",
            resource_type="delivery",
            resource_id=delivery.id,
            request_id=request.state.request_id,
        )


@router.post("/deliveries/{delivery_id}/cancel", status_code=204)
async def cancel_delivery(
    delivery_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        delivery = await session.get(Delivery, delivery_id)
        if delivery is None:
            raise AppError("not_found", "Delivery not found", 404)
        if delivery.status not in {DeliveryStatus.PENDING.value, DeliveryStatus.RETRY_WAIT.value}:
            raise AppError(
                "invalid_state", "Delivery cannot be cancelled from its current state", 409
            )
        delivery.status = DeliveryStatus.CANCELLED.value
        delivery.updated_at = request.app.state.clock.now()
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="delivery.cancel",
            resource_type="delivery",
            resource_id=delivery.id,
            request_id=request.state.request_id,
        )


@router.post("/people", status_code=201)
async def create_person(
    payload: PersonCreate, request: Request, admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    now = request.app.state.clock.now()
    person = Person(
        id=payload.id or new_id("person"),
        display_name=payload.display_name,
        active=True,
        is_default=payload.is_default,
        created_at=now,
        updated_at=now,
    )
    async with request.app.state.session_factory() as session, session.begin():
        if payload.is_default:
            await session.execute(update(Person).values(is_default=False, updated_at=now))
        session.add(person)
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="person.create",
            resource_type="person",
            resource_id=person.id,
            request_id=request.state.request_id,
        )
    return {
        "data": {
            "id": person.id,
            "name": person.display_name,
            "display_name": person.display_name,
            "active": person.active,
            "enabled": person.active,
            "is_default": person.is_default,
        },
        "request_id": request.state.request_id,
    }


@router.get("/people")
async def list_people(
    request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        people = (
            await session.scalars(select(Person).options(selectinload(Person.identities)))
        ).all()
        data = [
            {
                "id": p.id,
                "name": p.display_name,
                "display_name": p.display_name,
                "active": p.active,
                "enabled": p.active,
                "is_default": p.is_default,
                "wecom_identities": [
                    {"id": i.id, "user_id": i.user_id, "active": i.active} for i in p.identities
                ],
            }
            for p in people
        ]
    return {"data": data, "request_id": request.state.request_id}


@router.patch("/people/{person_id}")
async def update_person(
    person_id: str, payload: PersonUpdate, request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    async with request.app.state.session_factory() as session, session.begin():
        person = await session.get(Person, person_id)
        if person is None:
            raise AppError("not_found", "Person not found", 404)
        if payload.is_default:
            await session.execute(
                update(Person)
                .where(Person.id != person_id)
                .values(is_default=False, updated_at=request.app.state.clock.now())
            )
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(person, key, value)
        person.updated_at = request.app.state.clock.now()
    return {
        "data": {
            "id": person.id,
            "name": person.display_name,
            "display_name": person.display_name,
            "active": person.active,
            "enabled": person.active,
            "is_default": person.is_default,
        },
        "request_id": request.state.request_id,
    }


@router.post("/people/{person_id}/wecom-identities", status_code=201)
async def add_identity(
    person_id: str,
    payload: IdentityCreate,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    now = request.app.state.clock.now()
    async with request.app.state.session_factory() as session, session.begin():
        if await session.get(Person, person_id) is None:
            raise AppError("not_found", "Person not found", 404)
        identity = WeComIdentity(
            id=new_id("wid"),
            person_id=person_id,
            user_id=payload.user_id,
            active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(identity)
    return {
        "data": {"id": identity.id, "user_id": identity.user_id},
        "request_id": request.state.request_id,
    }


@router.delete("/people/{person_id}/wecom-identities/{identity_id}", status_code=204)
async def delete_identity(
    person_id: str, identity_id: str, request: Request, _admin: Admin = Depends(require_admin)
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        identity = await session.get(WeComIdentity, identity_id)
        if identity is None or identity.person_id != person_id:
            raise AppError("not_found", "WeCom identity not found", 404)
        await session.delete(identity)


@router.delete("/people/{person_id}", status_code=204)
async def delete_person(
    person_id: str, request: Request, admin: Admin = Depends(require_admin)
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        person = await session.get(Person, person_id)
        if person is None:
            raise AppError("not_found", "Person not found", 404)

        # 解绑所有企业微信绑定
        await session.execute(delete(WeComIdentity).where(WeComIdentity.person_id == person_id))
        # 删除接收人
        await session.delete(person)

        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action="person.delete",
            resource_type="person",
            resource_id=person_id,
            request_id=request.state.request_id,
        )
