from collections.abc import AsyncIterator

from app.config import Settings
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(settings: Settings) -> AsyncEngine:
    settings.ensure_sqlite_parent()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    if settings.database_url.startswith("sqlite"):
        timeout = settings.sqlite_busy_timeout_ms

        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={timeout}")
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def session_dependency(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
