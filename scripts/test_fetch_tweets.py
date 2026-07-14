import asyncio
import os
import sys
from pathlib import Path

# Add project root and backend directory to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


def load_env_manually() -> None:
    """Manually parse .env file in the current working directory to populate os.environ."""
    env_path = Path(".env")
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    os.environ.setdefault(k, v)
        except Exception as exc:
            print(f"Warning: failed to read .env file: {exc}")


load_env_manually()

from app.config import get_settings
from app.domain.clock import SystemClock
from app.infrastructure.security.secret_store import SecretStore
from plugins.builtin.codex_x_monitor.schemas import CodexXMonitorConfig
from plugins.builtin.codex_x_monitor.sources import TwscrapeSource
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class FakeContextForPrint:
    def __init__(self, secret_store) -> None:
        self.secret_store = secret_store

    async def get_secret(self, name: str) -> str:
        val = await self.secret_store.get("plugin", "codex_x_monitor", name)
        if val is None:
            raise ValueError(f"Secret {name} not found")
        return val


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

    store = SecretStore(
        factory, SystemClock(), settings.secret_encryption_key.get_secret_value()
    )
    context = FakeContextForPrint(store)

    config = CodexXMonitorConfig(
        username="thsottiaux", source="twscrape", twscrape_fetch_limit=10, include_replies=True
    )

    source = TwscrapeSource()
    print("Fetching tweets from X using your configured twscrape_cookie...")
    try:
        posts = await source.fetch(context, config)
        print(f"\nSuccessfully fetched {len(posts)} tweets from @{config.username}:\n")
        # Print up to 3 tweets
        for i, post in enumerate(posts[:3], 1):
            print(f"[{i}] Tweet ID: {post.id}")
            print(f"    Published At: {post.published_at}")
            print(f"    Is Reply: {post.is_reply} | Is Repost: {post.is_repost}")
            print(f"    URL: {post.url}")
            print(f"    Text: {post.text}")
            print("-" * 50)
    except Exception as e:
        print(f"Error fetching tweets: {e}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
