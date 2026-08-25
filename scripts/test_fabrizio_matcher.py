import asyncio
import sys
from pathlib import Path

# Add project root and backend directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from scripts.set_plugin_secret import load_env_manually

load_env_manually()

from app.application.x_source_service import XSourceService  # noqa: E402
from app.config import get_settings  # noqa: E402

from plugins.builtin.fabrizio_hwg_monitor.matcher import match_hwg  # noqa: E402
from plugins.builtin.fabrizio_hwg_monitor.schemas import FabrizioHwgConfig  # noqa: E402


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    settings = get_settings()
    source = XSourceService(settings)
    username = "FabrizioRomano"
    fetch_limit = 40

    print(
        f"Fetching the latest {fetch_limit} tweets from @{username} "
        f"via platform provider '{source.provider_name}'..."
    )
    try:
        posts = await source.fetch_records(username, limit=fetch_limit, include_replies=False)
    except Exception as exc:
        print(f"Error fetching timeline: {type(exc).__name__}: {exc}")
        await source.close()
        return

    if not posts:
        print("No tweets found or scraped.")
        await source.close()
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

    print("\n==================================================")
    print(f" MATCH TEST REPORT FOR @{username} ({len(posts)} TWEETS)")
    print("==================================================")
    print(f"Total Scraped: {len(posts)}")
    print(f"Total Matched: {len(matched_posts)}")
    print(f"Total Excluded: {len(unmatched_posts)}")
    print("==================================================")

    if matched_posts:
        print("\n🟢 MATCHED TWEETS:")
        for idx, post in enumerate(matched_posts):
            print(f"\n[{idx+1}] ID: {post.id} | Date: {post.published_at}")
            print(f"Content: {post.text}")
            print("-" * 50)
    else:
        print("\n🟡 NO TWEETS MATCHED.")

    print("\n🔴 EXCLUDED TWEETS (PREVIEW):")
    for _idx, (post, reason) in enumerate(
        unmatched_posts[:25]
    ):  # Show up to 25 exclusions for preview
        print(f" - [{post.id}] Reason: {reason} | Content: {post.text[:80].replace('\n', ' ')}...")

    if len(unmatched_posts) > 15:
        print(f" ... and {len(unmatched_posts) - 15} more excluded tweets.")

    await source.close()


if __name__ == "__main__":
    asyncio.run(main())
