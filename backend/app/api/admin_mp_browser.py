"""Admin API router for the MP Playwright browser publisher."""

from __future__ import annotations

from typing import Any, Literal

from app.api.admin_articles import serialize_article
from app.api.dependencies import require_mp_browser_actor
from app.infrastructure.database.models import Admin, ApiClient
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/mp-browser", tags=["admin-mp-browser"])

BrowserPhase = Literal["editing", "draft_saved", "publish_clicked"]
BrowserSessionState = Literal[
    "offline", "starting", "ready", "publishing", "auth_required", "error"
]
BrowserErrorCode = Literal[
    "AUTH_REQUIRED",
    "EDITOR_NOT_FOUND",
    "EDITOR_TIMEOUT",
    "CONTENT_REJECTED",
    "COVER_FAILED",
    "DRAFT_SAVE_FAILED",
    "PUBLISH_QUOTA_EXHAUSTED",
    "PUBLISH_CONFIRM_FAILED",
    "PUBLISH_RESULT_UNKNOWN",
    "PROVIDER_UI_CHANGED",
    "NETWORK_ERROR",
    "CLAIM_TIMEOUT_EXCEEDED",
    "MAX_ATTEMPTS_EXCEEDED",
]


class BrowserCheckpointRequest(BaseModel):
    phase: BrowserPhase
    draft_url: str | None = Field(default=None, max_length=2048)
    provider_draft_id: str | None = Field(default=None, max_length=200)


class BrowserCompleteRequest(BaseModel):
    provider_draft_id: str | None = Field(default=None, max_length=200)
    provider_publish_id: str | None = Field(default=None, max_length=200)
    published_url: str | None = Field(default=None, max_length=2048)


class BrowserFailRequest(BaseModel):
    retryable: bool
    error_code: str = Field(min_length=1, max_length=100)
    error_message: str = Field(default="", max_length=2000)


class BrowserSessionReport(BaseModel):
    state: BrowserSessionState
    current_article_id: str | None = Field(default=None, max_length=100)
    incident_id: str | None = Field(default=None, max_length=100)
    last_error_code: str | None = Field(default=None, max_length=100)
    last_error_message: str | None = Field(default=None, max_length=1000)
    qr_png_base64: str | None = None


def _format_article_for_browser(article: Any) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "author": article.author,
        "digest": article.digest,
        "content": article.content,
        "content_html": article.content_html,
        "cover_url": article.cover_url,
        "source_url": article.source_url,
    }


@router.post("/claim")
async def claim_article(
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> Any:
    service = request.app.state.mp_browser_service
    claimed = await service.claim_article()
    if claimed is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    article, attempt, resume, draft_url = claimed
    return {
        "data": {
            "article": _format_article_for_browser(article),
            "attempt": attempt,
            "resume": resume,
            "draft_url": draft_url,
        },
        "request_id": request.state.request_id,
    }


@router.post("/articles/{article_id}/checkpoint")
async def checkpoint_article(
    article_id: str,
    payload: BrowserCheckpointRequest,
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> dict[str, object]:
    service = request.app.state.mp_browser_service
    article = await service.checkpoint_article(
        article_id,
        phase=payload.phase,
        draft_url=payload.draft_url,
        provider_draft_id=payload.provider_draft_id,
    )
    return {"data": serialize_article(article), "request_id": request.state.request_id}


@router.post("/articles/{article_id}/complete")
async def complete_article(
    article_id: str,
    payload: BrowserCompleteRequest,
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> dict[str, object]:
    service = request.app.state.mp_browser_service
    article = await service.complete_article(
        article_id,
        provider_draft_id=payload.provider_draft_id,
        provider_publish_id=payload.provider_publish_id,
        published_url=payload.published_url,
    )
    return {"data": serialize_article(article), "request_id": request.state.request_id}


@router.post("/articles/{article_id}/fail")
async def fail_article(
    article_id: str,
    payload: BrowserFailRequest,
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> dict[str, object]:
    service = request.app.state.mp_browser_service
    article = await service.fail_article(
        article_id,
        retryable=payload.retryable,
        error_code=payload.error_code,
        error_message=payload.error_message,
    )
    return {"data": serialize_article(article), "request_id": request.state.request_id}


@router.post("/articles/{article_id}/release-auth")
async def release_article_for_auth(
    article_id: str,
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> dict[str, object]:
    service = request.app.state.mp_browser_service
    article = await service.release_for_auth(article_id)
    return {"data": serialize_article(article), "request_id": request.state.request_id}


@router.post("/session")
async def update_session(
    payload: BrowserSessionReport,
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> dict[str, object]:
    service = request.app.state.mp_browser_service
    snapshot = await service.update_session(
        state=payload.state,
        current_article_id=payload.current_article_id,
        incident_id=payload.incident_id,
        last_error_code=payload.last_error_code,
        last_error_message=payload.last_error_message,
        qr_png_base64=payload.qr_png_base64,
    )
    return {
        "data": {
            "state": snapshot.state,
            "last_seen_at": snapshot.last_seen_at.isoformat() if snapshot.last_seen_at else None,
            "current_article_id": snapshot.current_article_id,
            "incident_id": snapshot.incident_id,
            "last_error_code": snapshot.last_error_code,
            "last_error_message": snapshot.last_error_message,
            "qr_data_url": snapshot.qr_data_url,
        },
        "request_id": request.state.request_id,
    }


@router.get("/session")
async def get_session(
    request: Request,
    _actor: Admin | ApiClient = Depends(require_mp_browser_actor),
) -> dict[str, object]:
    service = request.app.state.mp_browser_service
    snapshot = await service.get_session()
    return {
        "data": {
            "state": snapshot.state,
            "last_seen_at": snapshot.last_seen_at.isoformat() if snapshot.last_seen_at else None,
            "current_article_id": snapshot.current_article_id,
            "incident_id": snapshot.incident_id,
            "last_error_code": snapshot.last_error_code,
            "last_error_message": snapshot.last_error_message,
            "qr_data_url": snapshot.qr_data_url,
        },
        "request_id": request.state.request_id,
    }
