from __future__ import annotations

from datetime import datetime
from typing import Annotated

from app.api.dependencies import get_session, require_admin
from app.api.errors import AppError
from app.application.media_service import MediaService
from app.infrastructure.database.models import Admin
from app.media.errors import MediaError
from app.media.validation import MediaKind
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/admin/media", tags=["admin-media"])


class MediaAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    mime_type: str
    checksum_sha256: str
    size_bytes: int
    duration_seconds: float | None
    source: str
    created_at: datetime
    expires_at: datetime | None


class MediaDownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    kind: MediaKind


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _service(request: Request) -> MediaService:
    service = getattr(request.app.state, "media_service", None)
    if not isinstance(service, MediaService):
        raise AppError("media_unavailable", "Media service is unavailable", 503)
    return service


def _map_error(exc: MediaError) -> AppError:
    status = 404 if exc.code == "media_not_found" else 422
    if exc.retryable:
        status = 503
    return AppError(exc.code, str(exc), status)


@router.post("", response_model=MediaAssetResponse, status_code=201)
async def upload_media(
    request: Request,
    kind: Annotated[MediaKind, Form()],
    file: Annotated[UploadFile, File()],
    admin: Annotated[Admin, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaAssetResponse:
    service = _service(request)
    data = await file.read(service.limit_for(kind) + 1)
    try:
        asset = await service.create(session, data, kind, source="upload", created_by=admin.id)
    except MediaError as exc:
        raise _map_error(exc) from exc
    return MediaAssetResponse.model_validate(asset)


@router.post("/download", response_model=MediaAssetResponse, status_code=201)
async def download_media(
    payload: MediaDownloadRequest,
    request: Request,
    admin: Annotated[Admin, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaAssetResponse:
    try:
        asset = await _service(request).create_from_url(
            session, payload.url, payload.kind, created_by=admin.id
        )
    except MediaError as exc:
        raise _map_error(exc) from exc
    return MediaAssetResponse.model_validate(asset)


@router.post("/tts", response_model=MediaAssetResponse, status_code=201)
async def create_tts_media(
    payload: TtsRequest,
    request: Request,
    admin: Annotated[Admin, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaAssetResponse:
    service = getattr(request.app.state, "tts_media_service", None)
    if service is None:
        raise AppError("tts_unavailable", "TTS adapter is not configured", 503)
    try:
        asset = await service.create_voice(session, payload.text, created_by=admin.id)
    except Exception as exc:
        raise AppError("tts_failed", "TTS media generation failed", 422) from exc
    return MediaAssetResponse.model_validate(asset)


@router.get("", response_model=list[MediaAssetResponse])
async def list_media(
    request: Request,
    _admin: Annotated[Admin, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MediaAssetResponse]:
    assets = await _service(request).list(session, limit=limit, offset=offset)
    return [MediaAssetResponse.model_validate(asset) for asset in assets]


@router.get("/{asset_id}", response_model=MediaAssetResponse)
async def get_media(
    asset_id: str,
    request: Request,
    _admin: Annotated[Admin, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaAssetResponse:
    try:
        asset = await _service(request).get(session, asset_id)
    except MediaError as exc:
        raise _map_error(exc) from exc
    return MediaAssetResponse.model_validate(asset)


@router.delete("/{asset_id}", status_code=204)
async def delete_media(
    asset_id: str,
    request: Request,
    _admin: Annotated[Admin, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    service = _service(request)
    try:
        asset = await service.get(session, asset_id)
        await service.delete(session, asset)
    except MediaError as exc:
        raise _map_error(exc) from exc
