"""Application service for the WeChat Official Account article workspace."""

from __future__ import annotations

from typing import Any

from app.api.errors import AppError
from app.channels.base import ChannelMessage
from app.channels.mp.render import render_wechat_html
from app.config import Settings
from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import (
    Delivery,
    MpArticle,
    MpArticleStatus,
    Notification,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

MAX_DIGEST_CHARS = 120


class MPArticleLibraryService:
    """Persist and manage article-library records for manual WeChat publishing."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._settings = settings

    async def store_from_delivery(
        self,
        *,
        delivery_id: str | None,
        message: ChannelMessage,
        status: str,
        provider_draft_media_id: str | None = None,
        provider_publish_id: str | None = None,
    ) -> str:
        """Create one article-library record per delivery (idempotent)."""
        now = self._clock.now()
        async with self._factory() as session:
            existing: MpArticle | None = (
                await session.scalar(select(MpArticle).where(MpArticle.delivery_id == delivery_id))
                if delivery_id
                else None
            )
            if existing is not None:
                return existing.id
            event_info = await self._load_event_info(session, delivery_id) if delivery_id else {}
            payload = dict(message.payload or {})
            digest = self._digest(message)
            content_html = render_wechat_html(
                content=message.content,
                cover_url=message.image_url,
                source_url=message.url,
            )
            article = MpArticle(
                id=new_id("mpa"),
                status=status,
                title=message.title,
                author=self._settings.mp_author,
                digest=digest,
                content=message.content,
                content_html=content_html,
                cover_url=message.image_url,
                source_url=message.url,
                event_key=event_info.get("event_key"),
                source_type=event_info.get("source_type"),
                source_id=event_info.get("source_id"),
                event_type=event_info.get("event_type"),
                notification_id=event_info.get("notification_id"),
                delivery_id=delivery_id,
                payload=payload,
                ai_profile=payload.get("article_ai_profile"),
                ai_status=payload.get("article_ai_status"),
                provider_draft_media_id=provider_draft_media_id,
                provider_publish_id=provider_publish_id,
                published_at=now if status == MpArticleStatus.PUBLISHED.value else None,
                created_at=now,
                updated_at=now,
            )
            session.add(article)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing_after_rollback: MpArticle | None = (
                    await session.scalar(
                        select(MpArticle).where(MpArticle.delivery_id == delivery_id)
                    )
                    if delivery_id
                    else None
                )
                if existing_after_rollback is not None:
                    return existing_after_rollback.id
                raise
            return article.id

    async def list_articles(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MpArticle], int]:
        async with self._factory() as session:
            base = select(MpArticle)
            if status:
                base = base.where(MpArticle.status == status)
            total = await session.scalar(select(func.count()).select_from(base.subquery()))
            items = list(
                await session.scalars(
                    base.order_by(MpArticle.created_at.desc(), MpArticle.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return items, int(total or 0)

    async def get_article(self, article_id: str) -> MpArticle | None:
        async with self._factory() as session:
            return await session.get(MpArticle, article_id)

    async def mark_published(self, article_id: str) -> MpArticle:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status == MpArticleStatus.PUBLISHED.value:
                return article
            if article.status not in {
                MpArticleStatus.DRAFT.value,
                MpArticleStatus.READY.value,
            }:
                raise AppError(
                    "invalid_status_transition",
                    "Only draft or ready articles can be marked as published",
                    409,
                )
            article.status = MpArticleStatus.PUBLISHED.value
            article.published_at = now
            article.updated_at = now
            return article

    async def mark_ignored(self, article_id: str) -> MpArticle:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status == MpArticleStatus.IGNORED.value:
                return article
            if article.status not in {
                MpArticleStatus.DRAFT.value,
                MpArticleStatus.READY.value,
            }:
                raise AppError(
                    "invalid_status_transition",
                    "Only draft or ready articles can be ignored",
                    409,
                )
            article.status = MpArticleStatus.IGNORED.value
            article.updated_at = now
            return article

    async def restore(self, article_id: str) -> MpArticle:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status == MpArticleStatus.READY.value:
                return article
            article.status = MpArticleStatus.READY.value
            article.updated_at = now
            return article

    async def _load_event_info(self, session: AsyncSession, delivery_id: str) -> dict[str, Any]:
        delivery = await session.scalar(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .options(selectinload(Delivery.notification).selectinload(Notification.event))
        )
        if delivery is None or delivery.notification is None:
            return {}
        notification = delivery.notification
        event = notification.event
        return {
            "notification_id": notification.id,
            "event_key": event.event_key if event is not None else None,
            "source_type": event.source_type if event is not None else None,
            "source_id": event.source_id if event is not None else None,
            "event_type": event.event_type if event is not None else None,
        }

    @staticmethod
    def _digest(message: ChannelMessage) -> str:
        candidate = message.payload.get("article_digest")
        if not isinstance(candidate, str) or not candidate.strip():
            candidate = " ".join(message.content.split())
        return candidate.strip()[:MAX_DIGEST_CHARS]
