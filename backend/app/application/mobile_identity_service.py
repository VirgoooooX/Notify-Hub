from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import jwt
from app.config import Settings
from app.domain.clock import Clock
from app.infrastructure.database.models import Person, WeComIdentity
from app.infrastructure.database.profile_models import ProfileMember
from app.infrastructure.security.tokens import (
    create_mobile_identity_token,
    decode_mobile_identity_token_context,
)
from app.profiles.constants import DEFAULT_PROFILE_ID
from app.profiles.errors import ProfileError
from app.profiles.routing import ProfileRoutingService
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
    profile_id: str = DEFAULT_PROFILE_ID


class MobileIdentityService:
    """Bridges a verified WeCom identity to short-lived mobile Web access."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock,
        routing: ProfileRoutingService | None = None,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._clock = clock
        self._routing = routing

    def issue(self, identity_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> str:
        return create_mobile_identity_token(
            identity_id, self._settings, self._clock, profile_id=profile_id
        )

    async def resolve(self, token: str, profile_id: str | None = None) -> MobileMember:
        try:
            identity_id, token_profile_id = decode_mobile_identity_token_context(
                token, self._settings
            )
        except jwt.PyJWTError as exc:
            raise MobileIdentityError("mobile entry token is invalid or expired") from exc
        effective_profile_id = profile_id or token_profile_id or DEFAULT_PROFILE_ID
        if (
            token_profile_id is not None
            and profile_id is not None
            and token_profile_id != profile_id
        ):
            raise MobileIdentityError("mobile entry token belongs to another profile")
        if self._routing is not None:
            try:
                await self._routing.resolve(effective_profile_id, capability="mobile_enabled")
            except ProfileError as exc:
                raise MobileIdentityError("mobile access is disabled for this profile") from exc
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
            if row is not None:
                member = await session.scalar(
                    select(ProfileMember).where(
                        ProfileMember.profile_id == effective_profile_id,
                        ProfileMember.person_id == row[1].id,
                        ProfileMember.enabled.is_(True),
                    )
                )
                if member is None:
                    profile_has_members = await session.scalar(
                        select(ProfileMember.id)
                        .where(ProfileMember.profile_id == effective_profile_id)
                        .limit(1)
                    )
                    if (
                        profile_has_members is not None
                        or effective_profile_id != DEFAULT_PROFILE_ID
                    ):
                        row = None
        if row is None:
            raise MobileIdentityError("WeCom identity is not linked or has been disabled")
        identity, person = row
        return MobileMember(
            identity.id, person.id, identity.user_id, person.display_name, effective_profile_id
        )

    async def identity_for_user(
        self, user_id: str, profile_id: str = DEFAULT_PROFILE_ID
    ) -> WeComIdentity | None:
        if self._routing is not None:
            try:
                await self._routing.resolve(profile_id, capability="mobile_enabled")
            except ProfileError as exc:
                raise MobileIdentityError("mobile access is disabled for this profile") from exc
        async with self._sessions() as session:
            identity = cast(
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
            if identity is None:
                return None
            member = await session.scalar(
                select(ProfileMember).where(
                    ProfileMember.profile_id == profile_id,
                    ProfileMember.person_id == identity.person_id,
                    ProfileMember.enabled.is_(True),
                )
            )
            if member is not None:
                return identity
            profile_has_members = await session.scalar(
                select(ProfileMember.id).where(ProfileMember.profile_id == profile_id).limit(1)
            )
            return (
                identity
                if profile_id == DEFAULT_PROFILE_ID and profile_has_members is None
                else None
            )
