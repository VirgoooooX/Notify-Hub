import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root and backend directory to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# Load env variables manually
from scripts.set_plugin_secret import load_env_manually

load_env_manually()

from app.application.event_service import EventService
from app.config import get_settings
from app.domain.clock import SystemClock
from app.infrastructure.database.models import WeComIdentity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


async def main() -> None:
    settings = get_settings()
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite:///./data/"):
        for p in [Path("data/notify-hub.db"), Path("backend/data/notify-hub.db")]:
            if p.is_file():
                db_url = "sqlite+aiosqlite:///" + str(p.resolve())
                break

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # 1. Detect cover image URL
    cover_url = "https://notify.198909.xyz:37891/codex_wechat_cover.png"

    # 2. Get active WeCom recipients from DB
    async with factory() as session:
        result = await session.scalars(
            select(WeComIdentity.person_id).where(WeComIdentity.active.is_(True))
        )
        recipients = list(result.all())

    if not recipients:
        print("Warning: No active WeCom users found in DB. Event might not deliver to anyone.")
        recipients = ["admin"]

    # 3. Simulate tweet contents
    tweet_text = (
        "Enjoy a full reset of your usage limits for ChatGPT Work and Codex. "
        "Propagating in the next hour. Rolling out to Pro plans first and then all paid plans over the next 24 hours."
    )
    summary = f"@thsottiaux 发布了与 Codex 用量重置相关的新消息:\n\n{tweet_text}"

    # 4. Invoke EventService to dispatch manual test event
    event_service = EventService(factory, SystemClock())

    print("--- Notify Hub - Simulating Codex X Monitor Reset Notification ---")
    print(f"Target Recipients: {recipients}")
    print(f"Cover Image URL:   {cover_url}")

    res = await event_service.accept_internal_event(
        source_type="plugin",
        source_id="codex_x_monitor",
        event_type="codex.usage_reset",
        event_key=f"x-post-test-manual-{int(datetime.now(UTC).timestamp())}",
        title="Codex 用量可能已重置",
        content=summary,
        recipients=recipients,
        message_type="article",
        level="info",
        occurred_at=datetime.now(UTC),
        url="https://x.com/thsottiaux/status/2076915116231275003",
        image_url=cover_url,
        payload={
            "post_id": "test_id_123",
            "author": "thsottiaux",
            "matched_rules": ["codex", "chatgpt work", "reset"],
            "source": "twscrape",
        },
    )

    print(f"\n[Success] Created simulated event: {res.event_id}")
    print("If your local 'start-dev' server is running, WeCom will deliver the card shortly!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
