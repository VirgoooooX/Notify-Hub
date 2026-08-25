import os
import sys
from pathlib import Path

# Add backend directory to path so we can import app modules.
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
                    # Strip quotes if present
                    v = v.strip().strip("'\"")
                    # Set in environ so Pydantic Settings picks it up
                    os.environ.setdefault(k, v)
        except Exception as exc:
            print(f"Warning: failed to read .env file: {exc}")


# Load environment variables from .env before initializing settings.
load_env_manually()

from app.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()

    cookie = settings.x_twscrape_cookie
    configured = bool(cookie and cookie.get_secret_value().strip())
    print("--- Notify Hub - platform X source configuration ---")
    print(f"Selected provider: {settings.x_source_provider}")
    print(f"Cold-standby Cookie configured: {'yes' if configured else 'no'}")
    print()
    print("This project no longer stores an X Cookie as a plugin Secret.")
    print("Configure the optional cold standby with:")
    print("  NOTIFY_HUB_X_SOURCE_PROVIDER=twscrape")
    print("  NOTIFY_HUB_X_TWSCRAPE_COOKIE=<complete Cookie>")
    print("Keep RSSHub as the normal provider and store its X Cookie in RSSHub.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
