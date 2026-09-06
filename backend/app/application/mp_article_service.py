"""Application service for the WeChat Official Account article workspace."""

from __future__ import annotations

from datetime import timedelta
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
            title = message.title
            content_lines = (message.content or "").strip().splitlines()
            if content_lines and content_lines[0].strip().startswith("# "):
                extracted_title = content_lines[0].strip().lstrip("# ").strip()
                if extracted_title:
                    title = extracted_title[:64]
            article = MpArticle(
                id=new_id("mpa"),
                status=status,
                title=title,
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
            return items, total or 0

    async def get_article(self, article_id: str) -> MpArticle | None:
        async with self._factory() as session:
            return await session.get(MpArticle, article_id)

    async def claim_for_browser(
        self,
    ) -> tuple[MpArticle, int, str, str | None] | None:
        if self._settings.mp_publish_mode != "browser":
            raise AppError("browser_mode_disabled", "MP browser publish mode is not active", 409)

        now = self._clock.now()
        timeout_delta = timedelta(seconds=self._settings.mp_browser_claim_timeout_seconds)
        max_attempts = self._settings.mp_browser_max_attempts

        async with self._factory() as session, session.begin():
            candidates = list(
                await session.scalars(
                    select(MpArticle)
                    .where(
                        MpArticle.status.in_(
                            [MpArticleStatus.READY.value, MpArticleStatus.PUBLISHING.value]
                        )
                    )
                    .order_by(MpArticle.created_at.asc(), MpArticle.id.asc())
                )
            )

            for article in candidates:
                payload = dict(article.payload or {})
                bp = dict(payload.get("browser_publish") or {})
                current_phase = bp.get("phase", "editing")
                draft_url = bp.get("draft_url")

                if article.status == MpArticleStatus.PUBLISHING.value:
                    if (now - article.updated_at) < timeout_delta:
                        continue

                    attempt = int(bp.get("attempt_count") or 1)
                    if current_phase == "publish_clicked":
                        resume = "reconcile"
                    else:
                        attempt += 1
                        if attempt > max_attempts:
                            article.status = MpArticleStatus.FAILED.value
                            bp["last_error_code"] = "CLAIM_TIMEOUT_EXCEEDED"
                            bp["last_error_message"] = (
                                f"Article exceeded maximum claim attempts ({max_attempts})"
                            )
                            payload["browser_publish"] = bp
                            article.payload = payload
                            article.updated_at = now
                            continue
                        if current_phase == "draft_saved" and draft_url:
                            resume = "resume"
                        else:
                            current_phase = "editing"
                            resume = "new"

                    bp["attempt_count"] = attempt
                    bp["phase"] = current_phase
                    bp["claimed_at"] = now.isoformat()
                    payload["browser_publish"] = bp
                    article.payload = payload
                    article.updated_at = now
                    return article, attempt, resume, draft_url

                if article.status == MpArticleStatus.READY.value:
                    attempt = int(bp.get("attempt_count") or 0) + 1
                    if attempt > max_attempts:
                        article.status = MpArticleStatus.FAILED.value
                        bp["last_error_code"] = "MAX_ATTEMPTS_EXCEEDED"
                        bp["last_error_message"] = (
                            f"Article exceeded maximum claim attempts ({max_attempts})"
                        )
                        payload["browser_publish"] = bp
                        article.payload = payload
                        article.updated_at = now
                        continue

                    if current_phase == "draft_saved" and draft_url:
                        resume = "resume"
                    else:
                        current_phase = "editing"
                        resume = "new"

                    bp["attempt_count"] = attempt
                    bp["phase"] = current_phase
                    bp["claimed_at"] = now.isoformat()
                    payload["browser_publish"] = bp
                    article.status = MpArticleStatus.PUBLISHING.value
                    article.payload = payload
                    article.updated_at = now
                    return article, attempt, resume, draft_url

            return None

    async def checkpoint_browser(
        self,
        article_id: str,
        *,
        phase: str,
        draft_url: str | None = None,
        provider_draft_id: str | None = None,
    ) -> MpArticle:
        if phase not in {"editing", "draft_saved", "publish_intent", "publish_clicked"}:
            raise AppError("invalid_phase", f"Invalid browser phase: {phase}", 422)

        now = self._clock.now()
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status != MpArticleStatus.PUBLISHING.value:
                raise AppError(
                    "invalid_status_transition",
                    "Only publishing articles can update browser checkpoint",
                    409,
                )

            payload = dict(article.payload or {})
            bp = dict(payload.get("browser_publish") or {})
            current_phase = bp.get("phase", "editing")
            phase_ranks = {
                "editing": 0,
                "draft_saved": 1,
                "publish_intent": 2,
                "publish_clicked": 3,
            }
            if phase_ranks[phase] < phase_ranks[current_phase]:
                raise AppError(
                    "invalid_phase_transition",
                    f"Cannot regress browser phase from {current_phase} to {phase}",
                    409,
                )

            if phase == "draft_saved":
                if not (draft_url or provider_draft_id):
                    raise AppError(
                        "missing_draft_identifier",
                        "draft_saved requires at least draft_url or provider_draft_id",
                        422,
                    )
                if draft_url:
                    bp["draft_url"] = draft_url
                if provider_draft_id:
                    article.provider_draft_media_id = provider_draft_id

            bp["phase"] = phase
            payload["browser_publish"] = bp
            article.payload = payload
            article.updated_at = now
            return article

    async def complete_browser_publish(
        self,
        article_id: str,
        *,
        status: str = MpArticleStatus.PUBLISHED.value,
        provider_draft_id: str | None = None,
        provider_publish_id: str | None = None,
        published_url: str | None = None,
    ) -> MpArticle:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status in {
                MpArticleStatus.PUBLISHED.value,
                MpArticleStatus.DRAFT.value,
            }:
                return article
            if article.status != MpArticleStatus.PUBLISHING.value:
                raise AppError(
                    "invalid_status_transition",
                    "Only publishing articles can be marked as completed",
                    409,
                )

            final_status = (
                MpArticleStatus.DRAFT.value
                if status == MpArticleStatus.DRAFT.value
                else MpArticleStatus.PUBLISHED.value
            )
            article.status = final_status
            if provider_draft_id:
                article.provider_draft_media_id = provider_draft_id
            if provider_publish_id:
                article.provider_publish_id = provider_publish_id
            if final_status == MpArticleStatus.PUBLISHED.value:
                article.published_at = now
            article.updated_at = now

            payload = dict(article.payload or {})
            bp = dict(payload.get("browser_publish") or {})
            if published_url:
                bp["published_url"] = published_url
            bp["last_error_code"] = None
            bp["last_error_message"] = None
            payload["browser_publish"] = bp
            article.payload = payload
            return article

    async def fail_browser_publish(
        self,
        article_id: str,
        *,
        retryable: bool,
        error_code: str,
        error_message: str,
    ) -> MpArticle:
        now = self._clock.now()
        max_attempts = self._settings.mp_browser_max_attempts
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status != MpArticleStatus.PUBLISHING.value:
                raise AppError(
                    "invalid_status_transition",
                    "Only publishing articles can fail",
                    409,
                )

            payload = dict(article.payload or {})
            bp = dict(payload.get("browser_publish") or {})
            bp["last_error_code"] = error_code
            bp["last_error_message"] = (error_message or "")[:500]

            phase = bp.get("phase", "editing")
            attempt = int(bp.get("attempt_count") or 1)

            if phase == "publish_clicked":
                article.status = MpArticleStatus.FAILED.value
            elif retryable and attempt < max_attempts:
                article.status = MpArticleStatus.READY.value
            else:
                article.status = MpArticleStatus.FAILED.value

            payload["browser_publish"] = bp
            article.payload = payload
            article.updated_at = now
            return article

    async def release_browser_for_auth(
        self,
        article_id: str,
    ) -> MpArticle:
        now = self._clock.now()
        async with self._factory() as session, session.begin():
            article = await session.get(MpArticle, article_id)
            if article is None:
                raise AppError("article_not_found", "Article not found", 404)
            if article.status == MpArticleStatus.PUBLISHING.value:
                payload = dict(article.payload or {})
                bp = dict(payload.get("browser_publish") or {})
                phase = bp.get("phase", "editing")
                if phase == "publish_clicked":
                    # Keep publishing so next claim is reconcile only
                    pass
                else:
                    article.status = MpArticleStatus.READY.value
                article.updated_at = now
            return article

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
                MpArticleStatus.FAILED.value,
            }:
                raise AppError(
                    "invalid_status_transition",
                    "Only draft, ready, or failed articles can be marked as published",
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
            if article.status == MpArticleStatus.PUBLISHING.value:
                raise AppError(
                    "invalid_status_transition",
                    "Cannot ignore an article that is currently publishing",
                    409,
                )
            if article.status not in {
                MpArticleStatus.DRAFT.value,
                MpArticleStatus.READY.value,
                MpArticleStatus.FAILED.value,
            }:
                raise AppError(
                    "invalid_status_transition",
                    "Only draft, ready, or failed articles can be ignored",
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
            if article.status == MpArticleStatus.PUBLISHING.value:
                raise AppError(
                    "invalid_status_transition",
                    "Cannot restore an article that is currently publishing",
                    409,
                )
            if article.status == MpArticleStatus.FAILED.value:
                bp = article.payload.get("browser_publish") or {}
                if bp.get("phase") == "publish_clicked":
                    raise AppError(
                        "cannot_restore_published_click",
                        (
                            "Cannot restore article after publish was clicked; "
                            "please verify in WeChat Official Account first"
                        ),
                        409,
                    )
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
