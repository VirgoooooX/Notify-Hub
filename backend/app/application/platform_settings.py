from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.infrastructure.database.models import PlatformSetting
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

XIAOHONGSHU_PUBLISHING_ENABLED_KEY = "xiaohongshu_publishing_enabled"
DEFAULT_XIAOHONGSHU_PUBLISHING_ENABLED = False


async def read_platform_setting(
    factory: async_sessionmaker[AsyncSession], key: str, default: Any
) -> Any:
    async with factory() as session:
        row = await session.get(PlatformSetting, key)
        return default if row is None else row.value


def is_platform_setting_enabled(value: object) -> bool:
    """Treat only JSON boolean true as enabled for safety switches."""

    return value is True


async def read_xiaohongshu_publishing_enabled(
    factory: async_sessionmaker[AsyncSession],
) -> bool:
    value = await read_platform_setting(
        factory,
        XIAOHONGSHU_PUBLISHING_ENABLED_KEY,
        DEFAULT_XIAOHONGSHU_PUBLISHING_ENABLED,
    )
    return is_platform_setting_enabled(value)


async def read_platform_timezone(factory: async_sessionmaker[AsyncSession], default: str) -> str:
    value = await read_platform_setting(factory, "timezone", default)
    if not isinstance(value, str):
        return default
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        # Legacy rows should never make requests fail; settings writes validate
        # IANA names, so this is only a defensive fallback for hand-edited DBs.
        return default
    return value
