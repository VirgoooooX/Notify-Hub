"""Codex X Monitor orchestration with durable, acknowledgement-safe cursors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .decision_prompt import (
    ARTICLE_GENERATION_INSTRUCTION,
    BEIJING_TZ,
    RESET_CLASSIFICATION_INSTRUCTION,
    XHS_NOTE_GENERATION_INSTRUCTION,
    build_article_content,
    build_classification_content,
    extract_xhs_title,
)
from .cover_generator import generate_dynamic_xhs_cover
from .matcher import detect_reset_kind, match_post
from .schemas import (
    PLUGIN_API_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    STATE_KEY,
    AIClassificationItem,
    ArticleDraft,
    CodexXMonitorConfig,
    EventDraft,
    MonitorState,
    PluginContext,
    PluginRunResult,
    PublishVariant,
    XPost,
)
from .sources import CodexRssHubSource, PostSource, RssAtomSource, TwscrapeSource, XApiSource

MAX_RECENT_PROCESSED_IDS = 200
NOTIFICATION_POST_MAX_LENGTH = 180


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


def format_post_summary(post: XPost, max_length: int | None = None) -> str:
    cleaned_lines: list[str] = []
    for raw_line in post.text.splitlines():
        line = raw_line.strip()
        while line.startswith(">"):
            line = line[1:].lstrip()
        heading = line.lstrip("#")
        if heading != line and heading.startswith(" "):
            continue
        if re.fullmatch(r"@[A-Za-z0-9_]+\s*原推[：:]?", line):
            continue
        if line:
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    if max_length is not None and len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    bj_time = post.published_at.astimezone(BEIJING_TZ).strftime("%m月%d日 %H:%M")
    prefix = f"Codex 可能有用量重置相关更新（北京时间 {bj_time}）。\n@{post.author_username} 原推："
    return f"{prefix}\n\n{text}"


class CodexXMonitorPlugin:
    plugin_id = PLUGIN_ID
    api_version = PLUGIN_API_VERSION
    version = PLUGIN_VERSION

    def __init__(self, sources: Mapping[str, PostSource] | None = None) -> None:
        self._sources: Mapping[str, PostSource] = sources or {
            "rsshub": CodexRssHubSource(),
            "rss": RssAtomSource(),
            "x_api": XApiSource(),
            "twscrape": TwscrapeSource(),
        }

    @classmethod
    def metadata(cls) -> dict[str, str]:
        return {"id": cls.plugin_id, "name": "Codex X Monitor", "version": cls.version}

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return CodexXMonitorConfig.model_json_schema()

    @classmethod
    def validate_config(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(config)
        # Provider selection moved to the platform X source integration. Keep
        # old rows readable, but never let a legacy plugin setting bypass it.
        normalized["source"] = "rsshub"
        if "fetch_limit" not in normalized and "twscrape_fetch_limit" in normalized:
            normalized["fetch_limit"] = normalized["twscrape_fetch_limit"]
        normalized.pop("twscrape_fetch_limit", None)
        normalized["feed_url"] = None
        validated = CodexXMonitorConfig.model_validate(normalized).model_dump(mode="json")
        validated.pop("twscrape_fetch_limit", None)
        return validated

    async def run(self, context: PluginContext) -> PluginRunResult:
        config = CodexXMonitorConfig.model_validate(await context.get_config())
        if not config.enabled:
            return PluginRunResult(status="disabled", message="plugin is disabled")

        source = self._sources[config.source]
        posts = sorted(await source.fetch(context, config), key=_post_sort_key)
        raw_state = await context.get_state(STATE_KEY, None)
        state = MonitorState.from_raw(raw_state, posts, max_recent_ids=MAX_RECENT_PROCESSED_IDS)

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
        rules_by_id = {
            post.id: match_post(post, config)
            for post in candidates
            if not post.is_repost and not post.is_reply
        }
        ai_candidates = []
        if config.decision_mode != "rules":
            for post in candidates:
                if post.is_repost or post.is_reply:
                    continue
                rule_result = rules_by_id[post.id]
                if (
                    config.decision_mode == "rules_then_ai"
                    and rule_result.confidence >= config.rule_ai_threshold
                ):
                    continue
                if config.decision_mode == "rules_or_ai" and rule_result.matched:
                    continue
                ai_candidates.append(post)

        ai_by_id: dict[str, Any] = {}
        for offset in range(0, len(ai_candidates), 5):
            batch = ai_candidates[offset : offset + 5]
            decisions = await context.ai.classify_many(
                profile=config.ai_profile,
                use_case="codex_usage_reset",
                instruction=RESET_CLASSIFICATION_INSTRUCTION,
                labels=["notify", "ignore", "uncertain"],
                items=[
                    AIClassificationItem(
                        id=post.id,
                        content=build_classification_content(post, posts),
                        cache_key=f"x:{post.author_username}:{post.id}",
                    )
                    for post in batch
                ],
            )
            ai_by_id.update({decision.id: decision for decision in decisions})

        for post in candidates:
            if post.is_repost:
                await self._checkpoint(context, state, post, config.source)
                continue
            if post.is_reply:
                await self._checkpoint(context, state, post, config.source)
                continue

            result = rules_by_id[post.id]
            ai_decision = ai_by_id.get(post.id)
            should_notify = result.matched
            ai_controls_decision = config.decision_mode == "ai" or ai_decision is not None
            if ai_controls_decision:
                should_notify = bool(
                    ai_decision is not None
                    and ai_decision.label == "notify"
                    and ai_decision.confidence >= config.ai_min_confidence
                )

            if should_notify:
                matched += 1
                summary = format_post_summary(post)
                content = format_post_summary(post, max_length=NOTIFICATION_POST_MAX_LENGTH)
                article_ai_status = "rules_summary"
                reset_kind = result.reset_kind or detect_reset_kind(post.text) or "direct"
                bj_display = post.published_at.astimezone(BEIJING_TZ).strftime("%m月%d日 %H:%M")
                event_title = (
                    f"Codex 可储存重置额度更新 ({bj_display})"
                    if reset_kind == "banked"
                    else f"Codex 用量重置更新 ({bj_display})"
                )
                mp_title = event_title
                mp_content = content
                if config.publish_to_wechat_mp and config.article_ai_profile:
                    try:
                        article_content = build_article_content(post, posts)
                        article_result = await context.ai.summarize(
                            profile=config.article_ai_profile,
                            use_case="codex_usage_reset_article",
                            content=article_content,
                            instruction=ARTICLE_GENERATION_INSTRUCTION,
                            max_characters=8000,
                            cache_key=f"x:{post.author_username}:{post.id}:article",
                        )
                        mp_content = article_result.summary.strip() or summary
                        if "\\n" in mp_content and "\n" not in mp_content:
                            mp_content = mp_content.replace("\\n", "\n")
                        article_ai_status = "ai_summarized"
                        lines = mp_content.strip().splitlines()
                        if lines and lines[0].startswith("# "):
                            candidate_title = lines[0].lstrip("# ").strip()
                            if candidate_title:
                                mp_title = candidate_title[:64]
                    except Exception as exc:
                        article_ai_status = "fallback_summary"
                        context.logger.warning(
                            "article_ai_summary_failed", post_id=post.id, error=str(exc)
                        )

                cover_url = (
                    str(config.cover_image_url) if config.cover_image_url is not None else None
                )
                if cover_url is None:
                    cover_url = context.media.public_static_url("codex_wechat_cover.png")

                publish_variants: list[PublishVariant] = []
                if config.publish_to_wechat_mp:
                    publish_variants.append(
                        PublishVariant(
                            platform="wechat_mp",
                            mode=config.wechat_mp_publish_mode,
                            title=mp_title,
                            body_text=mp_content,
                            image_urls=[cover_url] if cover_url else [],
                        )
                    )

                if config.publish_to_xiaohongshu:
                    xhs_ai_profile = config.xhs_article_ai_profile or config.article_ai_profile
                    xhs_content = summary
                    xhs_raw_title = event_title
                    if xhs_ai_profile:
                        try:
                            article_content = build_article_content(post, posts)
                            xhs_result = await context.ai.summarize(
                                profile=xhs_ai_profile,
                                use_case="codex_usage_reset_xhs",
                                content=article_content,
                                instruction=XHS_NOTE_GENERATION_INSTRUCTION,
                                max_characters=2000,
                                cache_key=f"x:{post.author_username}:{post.id}:xhs",
                            )
                            c = xhs_result.summary.strip()
                            if c:
                                if "\\n" in c and "\n" not in c:
                                    c = c.replace("\\n", "\n")
                                xhs_content = c
                                lines = xhs_content.splitlines()
                                if lines and lines[0].startswith("# "):
                                    xhs_raw_title = lines[0].lstrip("# ").strip()
                        except Exception as exc:
                            context.logger.warning(
                                "xhs_ai_summary_failed", post_id=post.id, error=str(exc)
                            )
                    xhs_title = extract_xhs_title(xhs_raw_title, bj_display, reset_kind)
                    if config.xhs_cover_image_url:
                        xhs_cover_url = str(config.xhs_cover_image_url)
                    else:
                        generated_rel = generate_dynamic_xhs_cover(xhs_title, post.id)
                        xhs_cover_url = context.media.public_static_url(generated_rel)

                    raw_tags = [t.strip("#") for t in re.findall(r"#([\w\u4e00-\u9fa5\-]+)", xhs_content)]
                    mandatory_topics = ["codex", "openai"]
                    extra_topics = [t for t in raw_tags if t.lower() not in mandatory_topics]
                    xhs_topics = list(dict.fromkeys(mandatory_topics + extra_topics))
                    # Strip trailing hashtag lines from body_text so they are not duplicated as plain text
                    clean_lines = [
                        line for line in xhs_content.splitlines()
                        if not re.match(r"^(?:#[\w\u4e00-\u9fa5\-]+(?:\s+|$))+$", line.strip())
                    ]
                    clean_body = "\n".join(clean_lines).strip()
                    publish_variants.append(
                        PublishVariant(
                            platform="xiaohongshu",
                            mode=config.xiaohongshu_publish_mode,
                            visibility=config.xiaohongshu_visibility,
                            title=xhs_title,
                            body_text=clean_body,
                            image_urls=[xhs_cover_url] if xhs_cover_url else [],
                            topics=xhs_topics,
                        )
                    )

                receipt = await context.emit_event(
                    EventDraft(
                        event_type="codex.usage_reset",
                        event_key=f"x-post-{post.id}",
                        title=event_title,
                        content=content,
                        level=config.notification_level,
                        occurred_at=post.published_at,
                        url=post.url,
                        image_url=cover_url,
                        recipients=config.recipients or None,
                        payload={
                            "post_id": post.id,
                            "author": post.author_username,
                            "matched_rules": list(result.matched_rules),
                            "rule_matched": result.matched,
                            "rule_confidence": result.confidence,
                            "rule_ai_threshold": config.rule_ai_threshold,
                            "source": config.source,
                            "reset_kind": reset_kind,
                            "decision_mode": config.decision_mode,
                            "ai_label": getattr(ai_decision, "label", None),
                            "ai_confidence": getattr(ai_decision, "confidence", None),
                            "ai_reason": getattr(ai_decision, "reason", None),
                            "article_ai_profile": (
                                config.article_ai_profile if config.publish_to_wechat_mp else None
                            ),
                            "article_ai_status": article_ai_status,
                        },
                        article=ArticleDraft(
                            title=event_title,
                            description=content,
                            url=post.url,
                            image_url=cover_url,
                        ),
                        publish_to_mp=config.publish_to_wechat_mp,
                        publish_variants=publish_variants,
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
        return [
            post
            for post in posts
            if post.id not in recent
            and (state.migration_cutoff_at is None or post.published_at > state.migration_cutoff_at)
        ]

    async def _checkpoint(
        self,
        context: PluginContext,
        state: MonitorState,
        post: XPost,
        source: str,
    ) -> None:
        if state.last_seen_post_id is None or int(post.id) > int(state.last_seen_post_id):
            state.last_seen_post_id = post.id
        if state.last_seen_published_at is None or post.published_at > state.last_seen_published_at:
            state.last_seen_published_at = post.published_at
        state.last_source = source  # type: ignore[assignment]
        state.recent_processed_ids = (
            [item for item in state.recent_processed_ids if item != post.id] + [post.id]
        )[-MAX_RECENT_PROCESSED_IDS:]
        await self._save_state(context, state)

    @staticmethod
    async def _save_state(context: PluginContext, state: MonitorState) -> None:
        await context.set_state(STATE_KEY, state.model_dump(mode="json"))
