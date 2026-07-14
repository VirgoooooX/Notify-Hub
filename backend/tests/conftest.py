from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from app.config import Settings
from app.infrastructure.database import Base
from app.main import create_app


@pytest_asyncio.fixture
async def api(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
    )
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app
    await app.state.engine.dispose()
