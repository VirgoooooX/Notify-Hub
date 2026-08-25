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

from app.application.x_source_service import XSourceService  # noqa: E402
from app.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    source = XSourceService(settings)
    username = "thsottiaux"
    print(f"Fetching tweets from @{username} via platform provider '{source.provider_name}'...")
    try:
        posts = await source.fetch_records(username, limit=10, include_replies=True)
        print(f"\nSuccessfully fetched {len(posts)} tweets from @{username}:\n")
        # Print up to 3 tweets
        for i, post in enumerate(posts[:3], 1):
            print(f"[{i}] Tweet ID: {post.id}")
            print(f"    Published At: {post.published_at}")
            print(f"    Is Reply: {post.is_reply} | Is Repost: {post.is_repost}")
            print(f"    URL: {post.url}")
            print(f"    Text: {post.text}")
            print("-" * 50)
    except Exception as exc:
        print(f"Error fetching tweets: {type(exc).__name__}: {exc}")
    finally:
        await source.close()


if __name__ == "__main__":
    asyncio.run(main())
