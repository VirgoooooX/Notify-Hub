from __future__ import annotations

from typing import Any

from app.api.dependencies import require_admin
from app.api.errors import AppError
from app.application.audit import add_audit
from app.application.notification_service import NotificationDraft
from app.application.wecom_menu_service import build_wecom_menu_payload
from app.infrastructure.database.models import Admin
from app.infrastructure.database.profile_models import WeComProfileConfig
from app.profiles.constants import DEFAULT_PROFILE_ID
from app.profiles.errors import ProfileError, ProfileNotFound
from app.profiles.service import (
    ProfileCreate,
    ProfileUpdate,
    WeComProfileUpdate,
)
from app.profiles.types import ProfileMetadata
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

router = APIRouter(prefix="/profiles", tags=["application-profiles"])


class ProfileCapabilitiesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outbound_enabled: bool | None = None
    callback_enabled: bool | None = None
    menu_enabled: bool | None = None
    conversation_enabled: bool | None = None
    interactive_enabled: bool | None = None
    mobile_enabled: bool | None = None
    broadcast_enabled: bool | None = None


class ProfileCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    is_default: bool = False
    capabilities: ProfileCapabilitiesInput | None = None
    agent_id: int | None = Field(default=None, ge=1)
    wecom_secret: SecretStr | None = None
    callback_token: SecretStr | None = None
    callback_aes_key: SecretStr | None = None


class ProfileUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    is_default: bool | None = None
    capabilities: ProfileCapabilitiesInput | None = None


class WeComProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    callback_enabled: bool | None = None
    wecom_secret: SecretStr | None = None
    callback_token: SecretStr | None = None
    callback_aes_key: SecretStr | None = None


class ProfileMemberInput(BaseModel):
    enabled: bool = True


class ProfileTestInput(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=128)


def _capabilities(body: ProfileCapabilitiesInput | None) -> dict[str, bool] | None:
    if body is None:
        return None
    return {key: value for key, value in body.model_dump().items() if value is not None}


def _serialize(profile: ProfileMetadata, wecom: dict[str, object] | None = None) -> dict[str, Any]:
    return {
        "id": profile.id,
        "key": profile.key,
        "name": profile.name,
        "enabled": profile.enabled,
        "is_default": profile.is_default,
        "capabilities": profile.capabilities.as_dict(),
        "wecom": wecom or {},
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


async def _wecom_status(request: Request, profile_id: str) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        config = await session.get(WeComProfileConfig, profile_id)
    store = request.app.state.secret_store
    settings = request.app.state.settings
    is_legacy_default = profile_id == DEFAULT_PROFILE_ID
    return {
        "agent_id": (
            config.agent_id
            if config is not None
            else settings.wecom_agent_id
            if is_legacy_default
            else None
        ),
        "agent_id_configured": bool(
            (config is not None and config.agent_id is not None)
            or (config is None and is_legacy_default and settings.wecom_agent_id is not None)
        ),
        "enabled": True if config is None else bool(config.enabled),
        "callback_enabled": bool(
            (config is not None and config.callback_enabled)
            or (config is None and is_legacy_default and settings.wecom_callback_token is not None)
        ),
        "secret_configured": bool(
            store is not None
            and await store.configured("application_profile", profile_id, "wecom_secret")
        )
        or (is_legacy_default and settings.wecom_secret is not None),
        "callback_token_configured": bool(
            store is not None
            and await store.configured("application_profile", profile_id, "wecom_callback_token")
        )
        or (is_legacy_default and settings.wecom_callback_token is not None),
        "callback_aes_key_configured": bool(
            store is not None
            and await store.configured("application_profile", profile_id, "wecom_callback_aes_key")
        )
        or (is_legacy_default and settings.wecom_callback_aes_key is not None),
    }


def _profile_error(exc: ProfileError) -> AppError:
    return AppError(exc.code.lower(), str(exc), 404 if isinstance(exc, ProfileNotFound) else 409)


@router.get("")
async def list_profiles(
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    profiles = await request.app.state.profile_registry.list_profiles()
    return {
        "data": {
            "items": [
                _serialize(profile, await _wecom_status(request, profile.id))
                for profile in profiles
            ]
        },
        "request_id": request.state.request_id,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreateInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    command = ProfileCreate(
        key=body.key,
        name=body.name,
        enabled=body.enabled,
        is_default=body.is_default,
        capabilities=_capabilities(body.capabilities),
        agent_id=body.agent_id,
        wecom_secret=(
            body.wecom_secret.get_secret_value() if body.wecom_secret is not None else None
        ),
        callback_token=(
            body.callback_token.get_secret_value() if body.callback_token is not None else None
        ),
        callback_aes_key=(
            body.callback_aes_key.get_secret_value() if body.callback_aes_key is not None else None
        ),
    )
    try:
        profile = await request.app.state.application_profile_service.create(command)
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(request, admin, "profile.create", profile.id)
    return {
        "data": _serialize(profile, await _wecom_status(request, profile.id)),
        "request_id": request.state.request_id,
    }


@router.get("/{profile_id}")
async def get_profile(
    profile_id: str,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        profile = await request.app.state.profile_registry.resolve(profile_id, allow_disabled=True)
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    return {
        "data": _serialize(profile, await _wecom_status(request, profile.id)),
        "request_id": request.state.request_id,
    }


@router.patch("/{profile_id}")
async def update_profile(
    profile_id: str,
    body: ProfileUpdateInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        profile = await request.app.state.application_profile_service.update(
            profile_id,
            ProfileUpdate(
                name=body.name,
                enabled=body.enabled,
                is_default=body.is_default,
                capabilities=_capabilities(body.capabilities),
            ),
        )
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(
        request,
        admin,
        "profile.disable" if body.enabled is False else "profile.update",
        profile.id,
    )
    return {
        "data": _serialize(profile, await _wecom_status(request, profile.id)),
        "request_id": request.state.request_id,
    }


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_profile(
    profile_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> None:
    try:
        await request.app.state.application_profile_service.delete(profile_id)
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(request, admin, "profile.delete", profile_id)


@router.patch("/{profile_id}/wecom")
async def configure_profile_wecom(
    profile_id: str,
    body: WeComProfileInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        result = await request.app.state.application_profile_service.configure_wecom(
            profile_id,
            WeComProfileUpdate(
                agent_id=body.agent_id,
                enabled=body.enabled,
                callback_enabled=body.callback_enabled,
                wecom_secret=(
                    body.wecom_secret.get_secret_value() if body.wecom_secret is not None else None
                ),
                callback_token=(
                    body.callback_token.get_secret_value()
                    if body.callback_token is not None
                    else None
                ),
                callback_aes_key=(
                    body.callback_aes_key.get_secret_value()
                    if body.callback_aes_key is not None
                    else None
                ),
            ),
        )
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(request, admin, "profile.wecom.configure", profile_id)
    return {
        "data": {**result, **await _wecom_status(request, profile_id)},
        "request_id": request.state.request_id,
    }


@router.get("/{profile_id}/wecom/menu/payload")
async def profile_wecom_menu_payload(
    profile_id: str,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        profile = await request.app.state.profile_registry.resolve(profile_id)
        await request.app.state.profile_routing.resolve(profile.id, capability="menu_enabled")
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    payload = build_wecom_menu_payload()
    return {
        "data": {
            "profile_id": profile.id,
            "payload": payload,
            "applied": False,
            "note": "Preview only. Use POST /wecom/menu/publish to apply it.",
        },
        "request_id": request.state.request_id,
    }


@router.post("/{profile_id}/wecom/menu/publish")
async def profile_wecom_menu_publish(
    profile_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        profile = await request.app.state.profile_registry.resolve(profile_id)
        publication = await request.app.state.wecom_menu_publication_service.publish(profile.id)
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(request, admin, "wecom.menu.publish", profile.id)
    if not publication.result.success:
        raise AppError(
            publication.result.error_code or "wecom_menu_publish_failed",
            publication.result.error_message or "WeCom rejected the menu publish",
            503 if publication.result.retryable else 502,
        )
    return {
        "data": {
            "profile_id": publication.profile_id,
            "payload": publication.payload,
            "applied": True,
        },
        "request_id": request.state.request_id,
    }


@router.post("/{profile_id}/wecom/test", status_code=202)
async def profile_wecom_test(
    profile_id: str,
    body: ProfileTestInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        profile = await request.app.state.profile_registry.resolve(profile_id)
        await request.app.state.profile_routing.resolve(profile.id, capability="outbound_enabled")
        notification_id = await request.app.state.notification_service.create(
            NotificationDraft(
                title=f"{profile.name} 测试",
                content="事件、队列与企业微信投递链路测试",
                message_type="text",
                recipients=[body.recipient_id],
                event_type="system.profile_test",
                profile_id=profile.id,
            )
        )
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(request, admin, "wecom.profile.test", profile.id)
    return {
        "data": {
            "profile_id": profile.id,
            "notification_id": notification_id,
            "status": "accepted",
        },
        "request_id": request.state.request_id,
    }


@router.get("/{profile_id}/members")
async def list_profile_members(
    profile_id: str,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        items = await request.app.state.application_profile_service.members(profile_id)
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    return {"data": {"items": items}, "request_id": request.state.request_id}


@router.put("/{profile_id}/members/{person_id}")
async def set_profile_member(
    profile_id: str,
    person_id: str,
    body: ProfileMemberInput,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    try:
        await request.app.state.application_profile_service.set_member(
            profile_id, person_id, enabled=body.enabled
        )
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    await _audit(
        request,
        admin,
        "profile.member.add" if body.enabled else "profile.member.disable",
        person_id,
        profile_id,
    )
    return {
        "data": {"profile_id": profile_id, "person_id": person_id, "enabled": body.enabled},
        "request_id": request.state.request_id,
    }


@router.delete("/{profile_id}/members/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile_member(
    profile_id: str,
    person_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> None:
    try:
        removed = await request.app.state.application_profile_service.remove_member(
            profile_id, person_id
        )
    except ProfileError as exc:
        raise _profile_error(exc) from exc
    if removed:
        await _audit(request, admin, "profile.member.remove", person_id, profile_id)


async def _audit(
    request: Request,
    admin: Admin,
    action: str,
    resource_id: str,
    profile_id: str | None = None,
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action=action,
            resource_type="application_profile",
            resource_id=resource_id,
            details={"profile_id": profile_id} if profile_id else None,
            request_id=request.state.request_id,
        )
