from typing import Any

from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import AuditLog
from sqlalchemy.ext.asyncio import AsyncSession


def add_audit(
    session: AsyncSession,
    clock: Clock,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            id=new_id("audit"),
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            request_id=request_id,
            created_at=clock.now(),
        )
    )
