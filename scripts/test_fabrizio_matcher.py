import asyncio
import os
import sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Add project root and backend directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from scripts.set_plugin_secret import load_env_manually
load_env_manually()

from app.config import get_settings
from app.infrastructure.database.models import Secret
from plugins.shared.x_monitor.twscrape_source import TwscrapeTimelineSource
from plugins.builtin.fabrizio_hwg_monitor.matcher import match_hwg
from plugins.builtin.fabrizio_hwg_monitor.schemas import FabrizioHwgConfig


class DummyContext:
    def __init__(self, cookie: str) -> None:
        self.cookie = cookie

    async def get_secret(self, key: str) -> str:
        if key == "twscrape_cookie":
            return self.cookie
        raise KeyError(key)


async def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    
    settings = get_settings()
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite:///./data/"):
        for p in [Path("data/notify-hub.db"), Path("backend/data/notify-hub.db")]:
            if p.is_file():
                db_url = "sqlite+aiosqlite:///" + str(p.resolve())
                break

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # 1. Retrieve twscrape_cookie
    cookie = os.environ.get("NOTIFY_HUB_PLUGIN_FABRIZIO_HWG_MONITOR_SECRET_TWSCRAPE_COOKIE")
    if not cookie:
        async with factory() as session:
            db_secret = await session.scalar(
                select(Secret.value_encrypted).where(
                    Secret.plugin_id == "fabrizio_hwg_monitor",
                    Secret.name == "twscrape_cookie"
                )
            )
            if db_secret:
                from app.infrastructure.security.secret_store import SecretStore
                store = SecretStore(settings.secret_key.get_secret_value())
                cookie = store.decrypt(db_secret)

    if not cookie:
        print("Error: twscrape_cookie is not configured.")
        await engine.dispose()
        return

    # 2. Fetch 40 tweets from @FabrizioRomano
    source = TwscrapeTimelineSource()
    dummy_ctx = DummyContext(cookie)
    username = "FabrizioRomano"
    fetch_limit = 40

    print(f"Connecting to X and scraping latest {fetch_limit} tweets from @{username}...")
    try:
        posts = await source.fetch(
            dummy_ctx,
            username=username,
            fetch_limit=fetch_limit,
            include_replies=False  # Typically we check main tweets first
        )
    except Exception as e:
        print(f"Error scraping timeline: {e}")
        await engine.dispose()
        return

    if not posts:
        print("No tweets found or scraped.")
        await engine.dispose()
        return

    # 3. Match each post using the plugin config
    config = FabrizioHwgConfig(
        username=username,
        include_replies=False,
        include_reposts=False,
    )

    matched_posts = []
    unmatched_posts = []

    for post in posts:
        if post.is_repost and not config.include_reposts:
            unmatched_posts.append((post, "Excluded (repost)"))
        elif post.is_reply and not config.include_replies:
            unmatched_posts.append((post, "Excluded (reply)"))
        elif not match_hwg(post.text):
            unmatched_posts.append((post, "Excluded (no match)"))
        else:
            matched_posts.append(post)

    print(f"\n==================================================")
    print(f" MATCH TEST REPORT FOR @{username} ({len(posts)} TWEETS)")
    print(f"==================================================")
    print(f"Total Scraped: {len(posts)}")
    print(f"Total Matched: {len(matched_posts)}")
    print(f"Total Excluded: {len(unmatched_posts)}")
    print(f"==================================================")

    if matched_posts:
        print("\n🟢 MATCHED TWEETS:")
        for idx, post in enumerate(matched_posts):
            print(f"\n[{idx+1}] ID: {post.id} | Date: {post.published_at}")
            print(f"Content: {post.text}")
            print("-" * 50)
    else:
        print("\n🟡 NO TWEETS MATCHED.")

    print("\n🔴 EXCLUDED TWEETS (PREVIEW):")
    for idx, (post, reason) in enumerate(unmatched_posts[:25]):  # Show up to 25 exclusions for preview
        print(f" - [{post.id}] Reason: {reason} | Content: {post.text[:80].replace('\n', ' ')}...")

    if len(unmatched_posts) > 15:
        print(f" ... and {len(unmatched_posts) - 15} more excluded tweets.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
