import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root and backend directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from scripts.set_plugin_secret import load_env_manually
load_env_manually()

from app.application.event_service import EventService
from app.application.media_service import MediaService
from app.config import get_settings
from app.domain.clock import SystemClock
from app.infrastructure.database.models import WeComIdentity, Secret
from app.infrastructure.security.tokens import generate_media_signature
from app.media.validation import MediaKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from plugins.shared.x_monitor.twscrape_source import TwscrapeTimelineSource
from plugins.shared.x_monitor.media import select_cover_image


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

    # 1. Retrieve twscrape_cookie from env or DB
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
        print("Error: twscrape_cookie is not configured in env or DB.")
        print("Please configure it in the Admin console plugins page first.")
        await engine.dispose()
        return

    # 2. Fetch active recipients
    async with factory() as session:
        result = await session.scalars(
            select(WeComIdentity.person_id).where(WeComIdentity.active.is_(True))
        )
        recipients = list(result.all())

    if not recipients:
        print("Warning: No active WeCom users found. Defaulting to 'admin'.")
        recipients = ["admin"]

    # 3. Fetch latest tweets using the shared timeline source
    source = TwscrapeTimelineSource()
    dummy_ctx = DummyContext(cookie)
    username = "FabrizioRomano"
    fetch_limit = 5

    print(f"Connecting to X and scraping latest {fetch_limit} tweets from @{username}...")
    try:
        posts = await source.fetch(
            dummy_ctx,
            username=username,
            fetch_limit=fetch_limit,
            include_replies=False
        )
    except Exception as e:
        print(f"Error scraping timeline: {e}")
        await engine.dispose()
        return

    if not posts:
        print("No tweets found or scraped.")
        await engine.dispose()
        return

    print("\n--- Scraped Tweets ---")
    for idx, post in enumerate(posts):
        print(f"[{idx}] ID: {post.id} | Date: {post.published_at}")
        print(f"Content: {post.text[:120]}...")
        print(f"Media URLs: {post.photo_urls}\n")

    # Pick the latest tweet
    target_post = posts[0]
    print(f"Selected latest tweet [{target_post.id}] to test:")
    print(f"Text: {target_post.text}")

    # 4. Proxy/Download cover image if present
    import httpx
    from app.media.storage import MediaStorage
    from app.media.downloader import SafeMediaDownloader
    from app.domain.clock import SystemClock

    media_storage = MediaStorage(settings.media_root)
    media_http = httpx.AsyncClient(follow_redirects=False)
    public_cover_url = None

    try:
        media_service = MediaService(
            media_storage,
            SystemClock(),
            downloader=SafeMediaDownloader(
                media_http,
                timeout_seconds=settings.media_download_timeout_seconds,
                max_redirects=settings.media_download_max_redirects,
            ),
            image_max_bytes=settings.media_image_max_bytes,
            voice_max_bytes=settings.media_voice_max_bytes,
            voice_max_seconds=settings.media_voice_max_seconds,
            retention_seconds=settings.media_retention_seconds,
        )
        original_cover = select_cover_image(target_post)
        if original_cover:
            print(f"\nDownloading and proxying original cover: {original_cover} ...")
            downloader = media_service.downloader
            if downloader:
                try:
                    data = await downloader.download(
                        original_cover,
                        max_bytes=media_service.limit_for(MediaKind.IMAGE)
                    )
                    try:
                        from app.media.processing import make_blurred_background_cover
                        data = make_blurred_background_cover(data)
                    except Exception as e:
                        print(f"Warning: Failed to process image blurred background: {e}")
                    async with factory() as session:
                        asset = await media_service.create(
                            session,
                            data,
                            MediaKind.IMAGE,
                            source="url",
                            created_by="test-script",
                            retention_seconds=3600
                        )
                    expires = int(datetime.now(UTC).timestamp()) + 3600
                    sig = generate_media_signature(
                        asset.id,
                        expires,
                        settings.public_media_signing_key.get_secret_value()
                    )
                    base_url = settings.public_base_url or "http://localhost:8000"
                    public_cover_url = f"{base_url.rstrip('/')}/public/media/{asset.id}?expires={expires}&sig={sig}"
                    print(f"Proxy URL generated: {public_cover_url}")
                except Exception as e:
                    print(f"Warning: Failed to download cover image: {e}")
    finally:
        await media_http.aclose()

    # 5. Dispatch manually via EventService
    event_service = EventService(factory, SystemClock())
    import time
    event_key = f"fabrizio-test-script-{target_post.id}-{int(time.time())}"
    title = "🚨 Live Fabrizio Test (No Match Required)"
    content = f"FabrizioRomano:\n\n{target_post.text}"

    print(f"\nDispatching event to recipients: {recipients}")
    res = await event_service.accept_internal_event(
        source_type="plugin",
        source_id="fabrizio_hwg_monitor",
        event_type="football.transfer_here_we_go",
        event_key=event_key,
        title=title,
        content=content,
        recipients=recipients,
        message_type="article",
        level="info",
        occurred_at=datetime.now(UTC),
        url=str(target_post.url) if target_post.url else None,
        image_url=public_cover_url,
        payload={
            "post_id": target_post.id,
            "author": target_post.author_username,
            "source": "test_script_no_match"
        }
    )

    print(f"\n[Success] Created simulated event: {res.event_id}")
    print("Check WeCom for the card notification!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
