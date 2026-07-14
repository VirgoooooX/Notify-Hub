import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add backend directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


def load_env_manually() -> None:
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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    settings = get_settings()

    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite:///./data/"):
        for p in [Path("data/notify-hub.db"), Path("backend/data/notify-hub.db")]:
            if p.is_file():
                db_url = "sqlite+aiosqlite:///" + str(p.resolve())
                break

    print(f"Connecting to database: {db_url}")
    engine = create_async_engine(db_url)

    plugin_id = "codex_x_monitor"

    # Default config structure for twscrape source
    config_data = {
        "enabled": True,
        "username": "thsottiaux",
        "source": "twscrape",
        "twscrape_fetch_limit": 40,
        "include_replies": True,
        "include_reposts": False,
        "notification_level": "info",
    }

    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT plugin_id FROM plugin_configs WHERE plugin_id = :pid"), {"pid": plugin_id}
        )
        exists = result.fetchone() is not None

        now_str = datetime.now(UTC).isoformat()
        config_json = json.dumps(config_data)

        if exists:
            await conn.execute(
                text(
                    "UPDATE plugin_configs SET config = :config, updated_at = :now WHERE plugin_id = :pid"
                ),
                {"config": config_json, "now": now_str, "pid": plugin_id},
            )
            print("Successfully updated existing config to 'twscrape' source!")
        else:
            await conn.execute(
                text(
                    "INSERT INTO plugin_configs (plugin_id, config, schema_version, updated_at) "
                    "VALUES (:pid, :config, 1, :now)"
                ),
                {"pid": plugin_id, "config": config_json, "now": now_str},
            )
            print("Successfully inserted new config with 'twscrape' source!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
