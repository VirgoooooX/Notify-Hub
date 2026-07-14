from app.api.dependencies import require_api_client
from app.api.errors import AppError
from app.api.schemas import EventCreate
from app.infrastructure.database.models import ApiClient
from fastapi import APIRouter, Depends, Header, Request, status

router = APIRouter(tags=["events"])


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def create_event(
    payload: EventCreate,
    request: Request,
    client: ApiClient = Depends(require_api_client),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    if idempotency_key is not None and idempotency_key != payload.event_key:
        raise AppError("idempotency_key_mismatch", "Idempotency-Key must match event_key", 422)
    result = await request.app.state.event_service.accept_api_event(client, payload)
    return {
        "data": {
            "event_id": result.event_id,
            "status": "accepted",
            "duplicate": result.duplicate,
        },
        "request_id": request.state.request_id,
    }
