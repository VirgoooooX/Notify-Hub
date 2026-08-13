from __future__ import annotations

from typing import Any

from app.api.dependencies import require_admin
from app.api.errors import AppError
from app.application.audit import add_audit
from app.infrastructure.database.models import Admin, MpArticle
from fastapi import APIRouter, Depends, Query, Request

router = APIRouter(tags=["admin-articles"])

VALID_STATUSES = {"draft", "ready", "published", "ignored"}


def _serialize(article: MpArticle) -> dict[str, Any]:
    return {
        "id": article.id,
        "status": article.status,
        "title": article.title,
        "author": article.author,
        "digest": article.digest,
        "content": article.content,
        "content_html": article.content_html,
        "cover_url": article.cover_url,
        "source_url": article.source_url,
        "event_key": article.event_key,
        "source_type": article.source_type,
        "source_id": article.source_id,
        "event_type": article.event_type,
        "notification_id": article.notification_id,
        "delivery_id": article.delivery_id,
        "ai_profile": article.ai_profile,
        "ai_status": article.ai_status,
        "provider_draft_media_id": article.provider_draft_media_id,
        "provider_publish_id": article.provider_publish_id,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "created_at": article.created_at.isoformat() if article.created_at else None,
        "updated_at": article.updated_at.isoformat() if article.updated_at else None,
        "payload": article.payload,
    }


@router.get("/articles")
async def list_articles(
    request: Request,
    _admin: Admin = Depends(require_admin),
    status: str | None = Query(default=None, max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    if status is not None and status not in VALID_STATUSES:
        raise AppError("invalid_status", "Unknown article status", 422)
    library = request.app.state.mp_article_library
    items, total = await library.list_articles(
        status=status,
        page=page,
        page_size=page_size,
    )
    return {
        "data": {
            "items": [_serialize(item) for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        "request_id": request.state.request_id,
    }


@router.get("/articles/config")
async def article_config(
    request: Request, _admin: Admin = Depends(require_admin)
) -> dict[str, object]:
    settings = request.app.state.settings
    configured = bool(settings.mp_app_id) and settings.mp_app_secret is not None
    effective_mode = (
        "library"
        if settings.mp_publish_mode == "library" or not configured
        else settings.mp_publish_mode
    )
    return {
        "data": {
            "configured": configured,
            "publish_mode": settings.mp_publish_mode,
            "effective_mode": effective_mode,
            "author": settings.mp_author,
            "mp_editor_url": "https://mp.weixin.qq.com",
        },
        "request_id": request.state.request_id,
    }


@router.get("/articles/{article_id}")
async def get_article(
    article_id: str,
    request: Request,
    _admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    article = await request.app.state.mp_article_library.get_article(article_id)
    if article is None:
        raise AppError("article_not_found", "Article not found", 404)
    return {"data": _serialize(article), "request_id": request.state.request_id}


@router.post("/articles/{article_id}/publish")
async def mark_published(
    article_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    article = await request.app.state.mp_article_library.mark_published(article_id)
    await _audit(request, admin, article_id, "article.publish")
    return {"data": _serialize(article), "request_id": request.state.request_id}


@router.post("/articles/{article_id}/ignore")
async def mark_ignored(
    article_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    article = await request.app.state.mp_article_library.mark_ignored(article_id)
    await _audit(request, admin, article_id, "article.ignore")
    return {"data": _serialize(article), "request_id": request.state.request_id}


@router.post("/articles/{article_id}/restore")
async def restore_article(
    article_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
) -> dict[str, object]:
    article = await request.app.state.mp_article_library.restore(article_id)
    await _audit(request, admin, article_id, "article.restore")
    return {"data": _serialize(article), "request_id": request.state.request_id}


async def _audit(
    request: Request,
    admin: Admin,
    article_id: str,
    action: str,
) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=admin.id,
            action=action,
            resource_type="mp_article",
            resource_id=article_id,
            request_id=request.state.request_id,
        )
