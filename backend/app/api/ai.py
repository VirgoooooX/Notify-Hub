from __future__ import annotations

from typing import Any, Literal

from app.ai.service import AIGatewayError, AIService
from app.api.dependencies import require_admin
from app.application.ai_control_service import (
    AIControlService,
    AIModelNotAllowedError,
    AIProfileInUseError,
    AIProviderInUseError,
    AIProviderUrlError,
    AIResourceConflictError,
    AIResourceNotFoundError,
)
from app.application.audit import add_audit
from app.infrastructure.database.ai_models import AIInvocation
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

ProviderPreset = Literal[
    "openai",
    "azure_openai",
    "gemini",
    "openrouter",
    "deepseek",
    "dashscope",
    "kimi",
    "zhipu",
    "siliconflow",
    "custom",
]
AIProtocol = Literal["openai_chat_completions", "openai_responses"]
StructuredMode = Literal["auto", "json_schema", "json_object", "prompt_json"]
AICapability = Literal["classify", "extract", "summarize"]
OutputLanguage = Literal["auto", "zh-CN", "en"]
ReasoningEffort = Literal["provider_default", "low", "medium", "high"]
Verbosity = Literal["concise", "standard", "detailed"]


class DataResponse(BaseModel):
    data: Any


class AIProviderCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str | None = Field(default=None, pattern=r"^aip_[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    preset: ProviderPreset
    protocol: AIProtocol = "openai_chat_completions"
    base_url: str = Field(min_length=8, max_length=2048)
    enabled: bool = True
    allow_private_network: bool = False
    timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    max_retries: int = Field(default=2, ge=0, le=5)
    verify_tls: bool = True
    structured_output_mode: StructuredMode = "auto"
    custom_query: dict[str, str] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("custom_query")
    @classmethod
    def validate_custom_query(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("custom_query supports at most 20 entries")
        if any(not key.strip() or len(key) > 100 or len(item) > 500 for key, item in value.items()):
            raise ValueError("custom_query contains an invalid entry")
        sensitive = ("key", "token", "secret", "signature", "password")
        if any(any(part in key.lower() for part in sensitive) for key in value):
            raise ValueError("sensitive query parameters must use SecretStore")
        return value


class AIProviderUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    preset: ProviderPreset | None = None
    protocol: AIProtocol | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    enabled: bool | None = None
    allow_private_network: bool | None = None
    timeout_seconds: float | None = Field(default=None, ge=1, le=300)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    verify_tls: bool | None = None
    structured_output_mode: StructuredMode | None = None
    custom_query: dict[str, str] | None = None

    @field_validator("custom_query")
    @classmethod
    def validate_custom_query(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return AIProviderCreate.validate_custom_query(value)


class AIProfileCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    capability: AICapability = "classify"
    provider_id: str = Field(pattern=r"^aip_[A-Za-z0-9_-]+$")
    model: str = Field(min_length=1, max_length=300)
    temperature: float = Field(default=0, ge=0, le=2)
    max_output_tokens: int = Field(default=160, ge=1, le=100000)
    response_format: StructuredMode = "auto"
    output_language: OutputLanguage = "auto"
    reasoning_effort: ReasoningEffort = "provider_default"
    verbosity: Verbosity = "standard"
    include_reason: bool = True
    max_reason_characters: int = Field(default=200, ge=0, le=1000)
    system_instructions: str = Field(default="", max_length=4000)
    timeout_seconds: float = Field(default=20, ge=1, le=300)
    cache_ttl_seconds: int = Field(default=2592000, ge=0, le=31536000)
    daily_request_limit: int | None = Field(default=None, ge=1, le=1000000)
    daily_token_limit: int | None = Field(default=None, ge=1, le=1000000000)
    enabled: bool = True


class AIProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    capability: AICapability | None = None
    provider_id: str | None = Field(default=None, pattern=r"^aip_[A-Za-z0-9_-]+$")
    model: str | None = Field(default=None, min_length=1, max_length=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=100000)
    response_format: StructuredMode | None = None
    output_language: OutputLanguage | None = None
    reasoning_effort: ReasoningEffort | None = None
    verbosity: Verbosity | None = None
    include_reason: bool | None = None
    max_reason_characters: int | None = Field(default=None, ge=0, le=1000)
    system_instructions: str | None = Field(default=None, max_length=4000)
    timeout_seconds: float | None = Field(default=None, ge=1, le=300)
    cache_ttl_seconds: int | None = Field(default=None, ge=0, le=31536000)
    daily_request_limit: int | None = Field(default=None, ge=1, le=1000000)
    daily_token_limit: int | None = Field(default=None, ge=1, le=1000000000)
    enabled: bool | None = None


class SecretInput(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class AllowedModelsInput(BaseModel):
    model_ids: list[str] = Field(default_factory=list, max_length=5000)

    @field_validator("model_ids")
    @classmethod
    def validate_model_ids(cls, value: list[str]) -> list[str]:
        cleaned = [model_id.strip() for model_id in value if model_id.strip()]
        if len(cleaned) != len(value) or len(cleaned) != len(set(cleaned)):
            raise ValueError("model_ids must be unique and non-empty")
        if any(len(model_id) > 300 for model_id in cleaned):
            raise ValueError("model_id is too long")
        return cleaned


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


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AIResourceNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "AI resource was not found")
    if isinstance(exc, AIResourceConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, AIProfileInUseError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, AIProviderInUseError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, AIProviderUrlError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, AIModelNotAllowedError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "AI resource operation failed")


async def _provider_items(request: Request) -> list[dict[str, Any]]:
    items = await _service(request).list_providers()
    store = request.app.state.secret_store
    for item in items:
        item["api_key_configured"] = bool(
            store is not None and await store.configured("ai_provider", item["id"], "api_key")
        )
    return items


@router.get("/providers")
async def list_providers(request: Request) -> DataResponse:
    return DataResponse(data=await _provider_items(request))


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(body: AIProviderCreate, request: Request) -> DataResponse:
    store = request.app.state.secret_store
    if body.api_key is not None and store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "secret store is not configured")
    try:
        result = await _service(request).create_provider(body.model_dump(exclude={"api_key"}))
    except (AIResourceConflictError, AIProviderUrlError) as exc:
        raise _translate_error(exc) from exc
    if body.api_key is not None:
        try:
            await store.put("ai_provider", result["id"], "api_key", body.api_key)
        except Exception:
            await _service(request).rollback_provider_creation(result["id"])
            raise
    result["api_key_configured"] = body.api_key is not None
    await _audit(request, "ai.provider.create", "ai_provider", result["id"])
    if body.api_key is not None:
        await _audit(request, "ai.provider.api_key.update", "ai_provider", result["id"])
    return DataResponse(data=result)


@router.patch("/providers/{provider_id}")
async def update_provider(
    provider_id: str, body: AIProviderUpdate, request: Request
) -> DataResponse:
    try:
        result = await _service(request).update_provider(
            provider_id, body.model_dump(exclude_none=True)
        )
    except (AIResourceNotFoundError, AIProviderUrlError) as exc:
        raise _translate_error(exc) from exc
    store = request.app.state.secret_store
    result["api_key_configured"] = bool(
        store is not None and await store.configured("ai_provider", provider_id, "api_key")
    )
    await _audit(request, "ai.provider.update", "ai_provider", provider_id)
    return DataResponse(data=result)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: str, request: Request) -> None:
    try:
        newly_deleted = await _service(request).delete_provider(provider_id)
    except (AIResourceNotFoundError, AIProviderInUseError) as exc:
        raise _translate_error(exc) from exc
    store = request.app.state.secret_store
    if store is not None:
        await store.delete("ai_provider", provider_id, "api_key")
    if newly_deleted:
        await _audit(request, "ai.provider.delete", "ai_provider", provider_id)


@router.put("/providers/{provider_id}/api-key")
async def put_provider_api_key(
    provider_id: str, body: SecretInput, request: Request
) -> DataResponse:
    if provider_id not in {item["id"] for item in await _service(request).list_providers()}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI provider was not found")
    store = request.app.state.secret_store
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "secret store is not configured")
    await store.put("ai_provider", provider_id, "api_key", body.value)
    await _audit(request, "ai.provider.api_key.update", "ai_provider", provider_id)
    return DataResponse(data={"configured": True})


@router.delete("/providers/{provider_id}/api-key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_api_key(provider_id: str, request: Request) -> None:
    store = request.app.state.secret_store
    if store is not None:
        await store.delete("ai_provider", provider_id, "api_key")
    await _audit(request, "ai.provider.api_key.delete", "ai_provider", provider_id)


@router.get("/providers/{provider_id}/models")
async def list_provider_models(provider_id: str, request: Request) -> DataResponse:
    try:
        models = await _service(request).list_provider_models(provider_id)
    except (AIResourceNotFoundError, ValueError) as exc:
        raise _translate_error(exc) from exc
    return DataResponse(data={"models": models})


@router.post("/providers/{provider_id}/models/sync")
async def sync_provider_models(provider_id: str, request: Request) -> DataResponse:
    try:
        discovered = await _gateway(request).list_models(provider_id)
        models = await _service(request).sync_provider_models(provider_id, discovered)
    except AIGatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.code) from exc
    except (AIResourceNotFoundError, ValueError) as exc:
        raise _translate_error(exc) from exc
    await _audit(request, "ai.provider.models.sync", "ai_provider", provider_id)
    return DataResponse(data={"models": models})


@router.put("/providers/{provider_id}/models/allowed")
async def set_allowed_provider_models(
    provider_id: str, body: AllowedModelsInput, request: Request
) -> DataResponse:
    try:
        models = await _service(request).set_allowed_models(provider_id, body.model_ids)
    except (AIResourceNotFoundError, AIModelNotAllowedError, ValueError) as exc:
        raise _translate_error(exc) from exc
    await _audit(request, "ai.provider.models.allowed.update", "ai_provider", provider_id)
    return DataResponse(data={"models": models})


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, request: Request) -> DataResponse:
    try:
        models = await _gateway(request).list_models(provider_id)
    except AIGatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.code) from exc
    return DataResponse(data={"status": "available", "model_count": len(models)})


@router.get("/profiles")
async def list_profiles(request: Request) -> DataResponse:
    return DataResponse(data=await _service(request).list_profiles())


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(body: AIProfileCreate, request: Request) -> DataResponse:
    try:
        result = await _service(request).create_profile(body.model_dump())
    except (AIResourceNotFoundError, AIResourceConflictError, AIModelNotAllowedError) as exc:
        raise _translate_error(exc) from exc
    await _audit(request, "ai.profile.create", "ai_profile", result["id"])
    return DataResponse(data=result)


@router.patch("/profiles/{profile_id}")
async def update_profile(profile_id: str, body: AIProfileUpdate, request: Request) -> DataResponse:
    try:
        result = await _service(request).update_profile(
            profile_id, body.model_dump(exclude_unset=True)
        )
    except (AIResourceNotFoundError, AIModelNotAllowedError) as exc:
        raise _translate_error(exc) from exc
    await _audit(request, "ai.profile.update", "ai_profile", profile_id)
    return DataResponse(data=result)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, request: Request) -> None:
    try:
        await _service(request).delete_profile(profile_id)
    except (AIResourceNotFoundError, AIProfileInUseError) as exc:
        raise _translate_error(exc) from exc
    await _audit(request, "ai.profile.delete", "ai_profile", profile_id)


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
