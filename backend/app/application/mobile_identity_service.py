from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import jwt
from app.config import Settings
from app.domain.clock import Clock
from app.infrastructure.database.models import Person, WeComIdentity
from app.infrastructure.security.tokens import (
    create_mobile_identity_token,
    decode_mobile_identity_token,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class MobileIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MobileMember:
    identity_id: str
    person_id: str
    wecom_user_id: str
    display_name: str


class MobileIdentityService:
    """Bridges a verified WeCom identity to short-lived mobile Web access."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._clock = clock

    def issue(self, identity_id: str) -> str:
        return create_mobile_identity_token(identity_id, self._settings, self._clock)

    async def resolve(self, token: str) -> MobileMember:
        try:
            identity_id = decode_mobile_identity_token(token, self._settings)
        except jwt.PyJWTError as exc:
            raise MobileIdentityError("mobile entry token is invalid or expired") from exc
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(WeComIdentity, Person)
                    .join(Person, Person.id == WeComIdentity.person_id)
                    .where(
                        WeComIdentity.id == identity_id,
                        WeComIdentity.active.is_(True),
                        Person.active.is_(True),
                    )
                )
            ).one_or_none()
        if row is None:
            raise MobileIdentityError("WeCom identity is not linked or has been disabled")
        identity, person = row
        return MobileMember(identity.id, person.id, identity.user_id, person.display_name)

    async def identity_for_user(self, user_id: str) -> WeComIdentity | None:
        async with self._sessions() as session:
            return cast(
                WeComIdentity | None,
                await session.scalar(
                    select(WeComIdentity)
                    .join(Person, Person.id == WeComIdentity.person_id)
                    .where(
                        WeComIdentity.user_id == user_id,
                        WeComIdentity.active.is_(True),
                        Person.active.is_(True),
                    )
                ),
            )
