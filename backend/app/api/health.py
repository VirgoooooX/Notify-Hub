from datetime import UTC, timedelta
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.api.errors import AppError
from app.infrastructure.database.models import WorkerHeartbeat
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(tags=["health"])


@lru_cache(maxsize=1)
def _migration_heads() -> frozenset[str]:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


@router.get("/health/live")
async def live() -> dict[str, object]:
    return {"data": {"status": "ok"}}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, object]:
    checks: dict[str, str] = {}
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        raise AppError("not_ready", "Database is unavailable", 503, {"checks": checks}) from None
    if request.app.state.settings.environment == "test":
        checks["migration"] = "not_required"
        checks["worker"] = "not_required"
        return {"data": {"status": "ready", "checks": checks}}
    try:
        async with request.app.state.session_factory() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            heartbeat = await session.get(WorkerHeartbeat, "delivery-main")
        checks["migration"] = "ok" if revision in _migration_heads() else "outdated"
        heartbeat_at = heartbeat.heartbeat_at if heartbeat is not None else None
        if heartbeat_at is not None and heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
        cutoff = request.app.state.clock.now() - timedelta(
            seconds=request.app.state.settings.worker_heartbeat_ttl_seconds
        )
        checks["worker"] = "ok" if heartbeat_at is not None and heartbeat_at >= cutoff else "stale"
    except Exception:
        checks["migration"] = "unavailable"
        checks["worker"] = "unavailable"
    if checks["migration"] != "ok" or checks["worker"] != "ok":
        raise AppError(
            "not_ready", "Migration or core worker is not ready", 503, {"checks": checks}
        )
    return {"data": {"status": "ready", "checks": checks}}
