from __future__ import annotations

import sys
from pathlib import Path

import structlog
from app.infrastructure.logging.setup import _structlog_processors

ROOT = Path(__file__).parents[2]


def test_container_migrations_use_the_application_database_working_directory() -> None:
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "alembic -c /app/backend/alembic.ini upgrade head" in entrypoint
    assert "cd /app/backend && alembic upgrade head" not in entrypoint
    assert "(cd /app && alembic -c /app/backend/alembic.ini upgrade head)" in entrypoint


def test_production_compose_uses_an_absolute_persistent_database_url() -> None:
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'NOTIFY_HUB_DATABASE_URL: "sqlite+aiosqlite:////app/data/notify-hub.db"' in compose


def test_exception_processor_renders_the_original_error() -> None:
    processors = _structlog_processors("UTC")
    assert structlog.processors.format_exc_info in processors

    try:
        raise RuntimeError("database is locked")
    except RuntimeError:
        event: dict[str, object] = {"exc_info": True}
        event = structlog.processors.format_exc_info(None, "error", event)

    assert "RuntimeError" in str(event["exception"])
    assert "database is locked" in str(event["exception"])
    assert "exc_info" not in event
    assert sys.exc_info() == (None, None, None)
