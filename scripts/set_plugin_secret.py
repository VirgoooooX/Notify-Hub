import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to path so we can import app modules
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


# Load environment variables from .env before initializing settings
load_env_manually()

from app.config import get_settings
from app.domain.clock import SystemClock
from app.infrastructure.security.secret_store import SecretStore
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


async def main() -> None:
    settings = get_settings()
    if not settings.secret_encryption_key:
        print("Error: NOTIFY_HUB_SECRET_ENCRYPTION_KEY is not configured in settings/env.")
        print("Please check that L:\\Web\\Notify Hub\\.env contains the variable:")
        print("NOTIFY_HUB_SECRET_ENCRYPTION_KEY=your-32-character-key")
        return

    # Check for local dev path misalignment (Uvicorn runs under backend/ directory)
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite:///./data/"):
        dev_db = Path("backend/data/notify-hub.db")
        if dev_db.is_file():
            db_url = "sqlite+aiosqlite:///" + str(dev_db.resolve())
            print(f"[Dev Mode] Redirecting database connection to: {dev_db}")

    # Create session factory
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    store = SecretStore(
        factory, SystemClock(), settings.secret_encryption_key.get_secret_value()
    )

    plugin_id = "codex_x_monitor"
    secret_name = "twscrape_cookie"

    print("--- Notify Hub - Plugin Secret Configurator ---")
    print(f"Setting secret '{secret_name}' for plugin '{plugin_id}'")
    print("Format must be: auth_token=xxxxxxxx; ct0=xxxxxxxx")
    print("-" * 47)

    try:
        secret_value = input("Please paste your Cookie value: ").strip()
    except KeyboardInterrupt:
        print("\nAborted.")
        return

    if not secret_value:
        print("Error: Secret value cannot be empty.")
        return

    if "auth_token=" not in secret_value or "ct0=" not in secret_value:
        print("Warning: Value does not seem to contain both 'auth_token' and 'ct0'.")
        confirm = input("Do you still want to save it? (y/N): ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    await store.put("plugin", plugin_id, secret_name, secret_value)
    print(f"\n[Success] Secret '{secret_name}' has been safely encrypted and saved to DB.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
