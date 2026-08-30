import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root and backend directory to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

try:
    from scripts.set_plugin_secret import load_env_manually
    load_env_manually()
except ImportError:
    pass

from app.ai.service import AIService
from app.application.event_service import EventService
from app.application.mp_article_service import MPArticleLibraryService
from app.channels.mp.adapter import MPArticleAdapter
from app.config import get_settings
from app.domain.clock import SystemClock
from app.infrastructure.database.ai_models import AIProfile, AIProvider
from app.infrastructure.database.models import Delivery, MpArticle, Notification
from app.infrastructure.database.plugin_models import PluginConfig
from app.infrastructure.security.secret_store import SecretStore
from plugins.builtin.codex_x_monitor.decision_prompt import (
    ARTICLE_GENERATION_INSTRUCTION,
    build_article_content,
)
from plugins.builtin.codex_x_monitor.schemas import ArticleDraft, EventDraft, XPost
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = SystemClock()
    secret_store = SecretStore(
        factory, clock, settings.secret_encryption_key.get_secret_value()
    )
    ai_service = AIService(factory, secret_store=secret_store)
    event_service = EventService(factory, clock)
    library_service = MPArticleLibraryService(factory, clock, settings)
    mp_adapter = MPArticleAdapter(
        client=None, settings=settings, library=library_service
    )

    print("--- 1. Ensuring AI Profile & Provider for Article Generation ---")
    async with factory() as session, session.begin():
        provider = await session.scalar(
            select(AIProvider).where(AIProvider.enabled.is_(True))
        )
        if provider is None:
            raise RuntimeError("No active AI Provider found in database")
        print(f"Using AI Provider: {provider.name} ({provider.base_url})")

        existing_summarizer = await session.scalar(
            select(AIProfile).where(
                AIProfile.capability == "summarize",
                AIProfile.enabled.is_(True),
            )
        )
        if existing_summarizer is not None:
            profile = existing_summarizer
            print(f"Using existing AI Profile '{profile.id}' ({profile.name}, model={profile.model})")
        else:
            now = clock.now()
            profile = AIProfile(
                id="article_summarizer",
                name="Codex Article Summarizer",
                provider_id=provider.id,
                model="Gemini 3.5 Flash",
                capability="summarize",
                temperature=0.5,
                max_output_tokens=2000,
                response_format="auto",
                timeout_seconds=30.0,
                cache_ttl_seconds=2592000,
                daily_request_limit=1000,
                daily_token_limit=1000000,
                enabled=True,
                system_instructions="",
                output_language="auto",
                reasoning_effort="provider_default",
                verbosity="standard",
                include_reason=False,
                max_reason_characters=200,
                description="AI Profile for WeChat Official Account article summarization",
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            print("Created AI Profile 'article_summarizer' (capability: summarize)")
        summarize_profile_id = profile.id

    print("\n--- 2. Preparing Codex X Monitor Posts Context ---")
    target_post = XPost(
        id="2076915116231275003",
        author_username="thsottiaux",
        author_display_name="Thomas Sottiaux",
        text="Hit the reset button for all ChatGPT Work and Codex users. Enjoy coding this weekend!",
        url="https://x.com/thsottiaux/status/2076915116231275003",
        published_at=datetime.now(UTC),
    )
    prequel_post = XPost(
        id="2076915116231275001",
        author_username="thsottiaux",
        author_display_name="Thomas Sottiaux",
        text="Thinking about giving everyone some extra quota today before the weekend rush...",
        url="https://x.com/thsottiaux/status/2076915116231275001",
        published_at=datetime.now(UTC),
    )
    timeline = [prequel_post, target_post]

    article_content = build_article_content(target_post, timeline)
    print("Article context payload built:")
    print(article_content)

    print("\n--- 3. Invoking AI Service Summarize Gateway ---")
    summary_result = await ai_service.summarize(
        profile=summarize_profile_id,
        plugin_id="codex_x_monitor",
        plugin_run_id=None,
        use_case="codex_usage_reset_article",
        content=article_content,
        instruction=ARTICLE_GENERATION_INSTRUCTION,
        max_characters=2000,
    )
    generated_text = summary_result.summary.strip()
    print("\n[AI Generated WeChat Article Text]:\n" + "=" * 50)
    print(generated_text)
    print("=" * 50)

    print("\n--- 4. Emitting Event with publish_to_mp=True to Core Pipeline ---")
    cover_url = (
        f"{settings.public_base_url.rstrip('/')}/codex_wechat_cover.png"
        if settings.public_base_url
        else "https://notify.198909.xyz:37891/codex_wechat_cover.png"
    )

    event_receipt = await event_service.accept_internal_event(
        source_type="plugin",
        source_id="codex_x_monitor",
        event_type="codex.usage_reset",
        event_key=f"x-post-{target_post.id}-{int(clock.now().timestamp())}",
        title="Codex 用量可能已重置",
        content=generated_text,
        level="info",
        occurred_at=target_post.published_at,
        url=str(target_post.url),
        image_url=cover_url,
        recipients=["admin"],
        message_type="article",
        payload={
            "post_id": target_post.id,
            "author": target_post.author_username,
            "source": "rsshub",
            "article_ai_profile": "article_summarizer",
            "article_ai_status": "ai_summarized",
        },
        publish_to_mp=True,
    )
    print(f"Event accepted: {event_receipt.event_id}")

    print("\n--- 5. Delivering to WeChat Article Library ---")
    async with factory() as session:
        delivery = await session.scalar(
            select(Delivery).where(
                Delivery.channel == "mp_article",
                Delivery.status == "pending",
            ).order_by(Delivery.created_at.desc())
        )
        if delivery is None:
            raise RuntimeError("Could not find pending mp_article delivery")

        # Deliver via adapter
        from app.channels.base import ChannelMessage

        notification = await session.get(Notification, delivery.notification_id)
        assert notification is not None

        channel_msg = ChannelMessage(
            delivery_id=delivery.id,
            message_type=notification.message_type,
            title=notification.title,
            content=notification.content,
            url=notification.url,
            image_url=notification.image_url,
            recipients=[],
            payload=notification.payload,
        )
        res = await mp_adapter.send(channel_msg)
        print(f"MP Adapter Delivery Result: success={res.success}, article_id={res.provider_message_id}")

        # Update delivery status
        delivery.status = "delivered" if res.success else "failed"
        await session.commit()

        # Query and display the newly created MpArticle
        article = await session.get(MpArticle, res.provider_message_id)
        if article:
            print("\n" + "#" * 60)
            print(f"[Success] Created WeChat Official Account Article Record:")
            print(f"  Article ID:   {article.id}")
            print(f"  Status:       {article.status}")
            print(f"  Title:        {article.title}")
            print(f"  Author:       {article.author}")
            print(f"  Digest:       {article.digest}")
            print(f"  Cover URL:    {article.cover_url}")
            print(f"  HTML Length:  {len(article.content_html)} chars")
            print("#" * 60)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
