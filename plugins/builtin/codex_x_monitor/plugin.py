"""Codex X Monitor orchestration with durable, acknowledgement-safe cursors."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .matcher import match_post
from .schemas import (
    PLUGIN_API_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    STATE_KEY,
    ArticleDraft,
    CodexXMonitorConfig,
    EventDraft,
    MonitorState,
    PluginContext,
    PluginRunResult,
    XPost,
)
from .sources import PostSource, RssAtomSource, XApiSource

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


def format_post_summary(post: XPost, max_length: int = 800) -> str:
    text = " ".join(post.text.split())
    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return f"@{post.author_username} 发布了与 Codex 用量重置相关的新消息:\n\n{text}"


class CodexXMonitorPlugin:
    plugin_id = PLUGIN_ID
    api_version = PLUGIN_API_VERSION
    version = PLUGIN_VERSION

    def __init__(self, sources: Mapping[str, PostSource] | None = None) -> None:
        self._sources: Mapping[str, PostSource] = sources or {
            "rss": RssAtomSource(),
            "x_api": XApiSource(),
        }

    @classmethod
    def metadata(cls) -> dict[str, str]:
        return {"id": cls.plugin_id, "name": "Codex X Monitor", "version": cls.version}

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return CodexXMonitorConfig.model_json_schema()

    async def run(self, context: PluginContext) -> PluginRunResult:
        config = CodexXMonitorConfig.model_validate(await context.get_config())
        if not config.enabled:
            return PluginRunResult(status="disabled", message="plugin is disabled")

        source = self._sources[config.source]
        posts = sorted(await source.fetch(context, config), key=_post_sort_key)
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

            result = match_post(post, config)
            if result.matched:
                matched += 1
                summary = format_post_summary(post)
                receipt = await context.emit_event(
                    EventDraft(
                        event_type="codex.usage_reset",
                        event_key=f"x-post-{post.id}",
                        title="Codex 用量可能已重置",
                        content=summary,
                        level=config.notification_level,
                        occurred_at=post.published_at,
                        url=post.url,
                        recipients=config.recipients or None,
                        payload={
                            "post_id": post.id,
                            "author": post.author_username,
                            "matched_rules": list(result.matched_rules),
                            "source": config.source,
                        },
                        article=ArticleDraft(
                            title="Codex 用量可能已重置",
                            description=summary,
                            url=post.url,
                        ),
                    )
                )
                status = _receipt_status(receipt)
                if status not in {"accepted", "duplicate"}:
                    receipt_status = status or "missing"
                    raise EmitEventError(f"core did not accept event (status={receipt_status})")
                if status == "accepted":
                    emitted += 1

            # Matching posts are checkpointed only after accepted/duplicate. Non-matches
            # are checkpointed immediately because they have been successfully scanned.
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
