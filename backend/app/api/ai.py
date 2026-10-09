from __future__ import annotations

from typing import Any

from app.ai.provider import AIProviderError
from app.ai.service import AIGatewayError, AIService
from app.api.dependencies import require_admin
from app.application.ai_control_service import (
    HUB_CONNECTION_ID,
    AIControlService,
    AIProviderUrlError,
)
from app.application.audit import add_audit
from app.infrastructure.database.ai_models import AIInvocation
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select


class DataResponse(BaseModel):
    data: Any


class AIHubUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    base_url: str = Field(min_length=8, max_length=2048)
    enabled: bool = True
    allow_private_network: bool = False
    timeout_seconds: float = Field(default=600, ge=1, le=1800)
    verify_tls: bool = True


class SecretInput(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_admin)])


def _service(request: Request) -> AIControlService:
    service = getattr(request.app.state, "ai_control_service", None)
    if not isinstance(service, AIControlService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI control plane is not ready")
    return service


def _gateway(request: Request) -> AIService:
    service = getattr(request.app.state, "ai_service", None)
    if not isinstance(service, AIService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI Gateway is not ready")
    return service


async def _audit(request: Request, action: str, resource_type: str, resource_id: str) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=request.state.admin_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=getattr(request.state, "request_id", None),
        )


async def _hub_view(request: Request) -> dict[str, Any]:
    result = await _service(request).get_connection()
    store = request.app.state.secret_store
    result["application_key_configured"] = bool(
        store is not None and await store.configured("ai_hub", HUB_CONNECTION_ID, "application_key")
    )
    return result


@router.get("/hub")
async def get_hub(request: Request) -> DataResponse:
    return DataResponse(data=await _hub_view(request))


@router.put("/hub")
async def update_hub(body: AIHubUpdate, request: Request) -> DataResponse:
    try:
        await _service(request).update_connection(body.model_dump())
    except AIProviderUrlError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await _audit(request, "ai.hub.update", "ai_hub", HUB_CONNECTION_ID)
    return DataResponse(data=await _hub_view(request))


@router.put("/hub/application-key")
async def put_application_key(body: SecretInput, request: Request) -> DataResponse:
    store = request.app.state.secret_store
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "secret store is not configured")
    await store.put("ai_hub", HUB_CONNECTION_ID, "application_key", body.value)
    await _service(request).invalidate_caches()
    await _audit(request, "ai.hub.application_key.update", "ai_hub", HUB_CONNECTION_ID)
    return DataResponse(data={"configured": True})


@router.delete("/hub/application-key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application_key(request: Request) -> None:
    store = request.app.state.secret_store
    if store is not None:
        await store.delete("ai_hub", HUB_CONNECTION_ID, "application_key")
    await _service(request).invalidate_caches()
    await _audit(request, "ai.hub.application_key.delete", "ai_hub", HUB_CONNECTION_ID)


@router.post("/hub/test")
async def test_hub(request: Request) -> DataResponse:
    try:
        models = await _gateway(request).test_connection()
    except AIGatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.code) from exc
    return DataResponse(data={"status": "available", "model_count": len(models)})


@router.get("/hub/routing")
async def export_hub_routing(request: Request) -> DataResponse:
    return DataResponse(data=await _service(request).export_hub_routing())


@router.get("/profiles")
async def list_profiles(request: Request) -> DataResponse:
    try:
        return DataResponse(data=await _service(request).list_profiles())
    except AIProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.code) from exc


@router.get("/invocations")
async def list_invocations(request: Request, limit: int = 100) -> DataResponse:
    safe_limit = min(max(limit, 1), 500)
    async with request.app.state.session_factory() as session:
        rows = await session.scalars(
            select(AIInvocation).order_by(AIInvocation.created_at.desc()).limit(safe_limit)
        )
        data = [
            {
                "id": row.id,
                "profile_id": row.profile_id,
                "plugin_id": row.plugin_id,
                "plugin_run_id": row.plugin_run_id,
                "use_case": row.use_case,
                "input_hash": row.input_hash,
                "cache_hit": row.cache_hit,
                "status": row.status,
                "latency_ms": row.latency_ms,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "error_code": row.error_code,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    return DataResponse(data=data)
