from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from plugins.shared.x_monitor.media import select_cover_image
from plugins.shared.x_monitor.twscrape_source import TwscrapeTimelineSource
from plugins.shared.x_monitor.models import XPost

from .matcher import match_hwg
from .schemas import (
    STATE_KEY,
    ArticleDraft,
    FabrizioHwgConfig,
    EventDraft,
    MonitorState,
    PluginContext,
    PluginRunResult,
)

MAX_RECENT_PROCESSED_IDS = 200


class EmitEventError(RuntimeError):
    pass


def _post_sort_key(post: XPost) -> tuple[datetime, int]:
    return post.published_at, int(post.id)


def _receipt_status(receipt: Any) -> str | None:
    if isinstance(receipt, Mapping):
        value = receipt.get("status")
    else:
        value = getattr(receipt, "status", None)
    return str(value).lower() if value is not None else None


def format_summary(post: XPost, max_length: int = 800) -> str:
    text = " ".join(post.text.split())
    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text


class FabrizioHwgPlugin:
    plugin_id = "fabrizio_hwg_monitor"
    api_version = "1"
    version = "0.1.0"

    def __init__(self, source: Any = None) -> None:
        self._source = source or TwscrapeTimelineSource()

    @classmethod
    def metadata(cls) -> dict[str, str]:
        return {"id": cls.plugin_id, "name": "Fabrizio HWG Monitor", "version": cls.version}

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return FabrizioHwgConfig.model_json_schema()

    async def run(self, context: PluginContext) -> PluginRunResult:
        config = FabrizioHwgConfig.model_validate(await context.get_config())
        if not config.enabled:
            return PluginRunResult(status="disabled", message="plugin is disabled")

        posts = sorted(
            await self._source.fetch(
                context,
                config.username,
                config.twscrape_fetch_limit,
                config.include_replies,
            ),
            key=_post_sort_key
        )
        raw_state = await context.get_state(STATE_KEY, None)
        state = MonitorState.model_validate(raw_state or {})

        if state.last_seen_post_id is None and config.first_run_mode == "baseline":
            if posts:
                latest = posts[-1]
                state.last_seen_post_id = latest.id
                state.last_seen_published_at = latest.published_at
                state.recent_processed_ids = [post.id for post in posts[-MAX_RECENT_PROCESSED_IDS:]]
            state.last_source = config.source
            state.last_success_at = datetime.now(UTC)
            await self._save_state(context, state)
            return PluginRunResult(status="baseline_initialized", fetched_posts=len(posts))

        candidates = self._new_candidates(posts, state)
        if state.last_seen_post_id is None and config.first_run_mode == "scan_recent":
            candidates = candidates[-config.scan_recent_limit :]

        emitted = 0
        matched = 0
        for post in candidates:
            if post.is_repost and not config.include_reposts:
                await self._checkpoint(context, state, post, config.source)
                continue
            if post.is_reply and not config.include_replies:
                await self._checkpoint(context, state, post, config.source)
                continue

            if not match_hwg(post.text):
                await self._checkpoint(context, state, post, config.source)
                continue

            matched += 1
            original_cover = select_cover_image(post)
            cover_url = None

            if original_cover:
                try:
                    cover_url = await context.media.publish_image_url(original_cover)
                except Exception as exc:
                    context.logger.warning(
                        "tweet_cover_proxy_failed",
                        post_id=post.id,
                        error=str(exc)
                    )
                    cover_url = str(config.fallback_cover_url) if config.fallback_cover_url else None
            else:
                cover_url = str(config.fallback_cover_url) if config.fallback_cover_url else None

            receipt = await context.emit_event(
                EventDraft(
                    event_type="football.transfer_here_we_go",
                    event_key=f"fabrizio-hwg-{post.id}",
                    title="🚨 HERE WE GO｜Fabrizio Romano",
                    content=format_summary(post),
                    level=config.notification_level,
                    url=post.url,
                    image_url=cover_url,
                    recipients=config.recipients or None,
                    payload={
                        "post_id": post.id,
                        "author": post.author_username,
                        "source": "twscrape",
                        "original_image_url": str(original_cover) if original_cover else None,
                    },
                    article=ArticleDraft(
                        title="🚨 HERE WE GO｜Fabrizio Romano",
                        description=format_summary(post),
                        url=post.url,
                        image_url=cover_url,
                    ),
                )
            )

            status = _receipt_status(receipt)
            if status not in {"accepted", "duplicate"}:
                receipt_status = status or "missing"
                raise EmitEventError(f"core did not accept event (status={receipt_status})")
            if status == "accepted":
                emitted += 1

            await self._checkpoint(context, state, post, config.source)

        state.last_source = config.source
        state.last_success_at = datetime.now(UTC)
        await self._save_state(context, state)
        return PluginRunResult(
            status="success",
            emitted_events=emitted,
            fetched_posts=len(posts),
            new_posts=len(candidates),
            matched_posts=matched,
        )

    @staticmethod
    def _new_candidates(posts: list[XPost], state: MonitorState) -> list[XPost]:
        recent = set(state.recent_processed_ids)
        if state.last_seen_post_id is None:
            return [post for post in posts if post.id not in recent]
        cursor = int(state.last_seen_post_id)
        return [post for post in posts if post.id not in recent and int(post.id) > cursor]

    async def _checkpoint(
        self,
        context: PluginContext,
        state: MonitorState,
        post: XPost,
        source: str,
    ) -> None:
        state.last_seen_post_id = post.id
        state.last_seen_published_at = post.published_at
        state.last_source = source  # type: ignore[assignment]
        state.recent_processed_ids = (
            [item for item in state.recent_processed_ids if item != post.id] + [post.id]
        )[-MAX_RECENT_PROCESSED_IDS:]
        await self._save_state(context, state)

    @staticmethod
    async def _save_state(context: PluginContext, state: MonitorState) -> None:
        await context.set_state(STATE_KEY, state.model_dump(mode="json"))
