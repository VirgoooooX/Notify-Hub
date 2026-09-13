from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models import Person, WeComIdentity
from app.infrastructure.database.profile_models import ProfileMember
from app.profiles.constants import DEFAULT_PROFILE_ID
from app.profiles.errors import ProfileCapabilityDisabled, ProfileError
from app.profiles.registry import ProfileRegistry


class ProfileRoutingService:
    """Centralize profile validation, membership and capability checks."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProfileRegistry,
    ) -> None:
        self._sessions = sessions
        self._registry = registry

    async def resolve(
        self, profile_ref: str | None = None, *, capability: str | None = None
    ) -> str:
        profile = await self._registry.resolve(profile_ref)
        if capability is not None and not profile.capabilities.allows(capability):
            raise ProfileCapabilityDisabled(f"{capability} is disabled for profile {profile.key!r}")
        return profile.id

    async def validate_recipients(self, profile_id: str, person_ids: Iterable[str]) -> None:
        requested = set(person_ids)
        if not requested:
            return
        async with self._sessions() as session:
            rows = set(
                await session.scalars(
                    select(ProfileMember.person_id)
                    .join(Person, Person.id == ProfileMember.person_id)
                    .where(
                        ProfileMember.profile_id == profile_id,
                        ProfileMember.enabled.is_(True),
                        Person.active.is_(True),
                        ProfileMember.person_id.in_(requested),
                    )
                )
            )
            if rows == requested:
                return
            # Direct Base.metadata.create_all tests do not run the migration
            # backfill. Preserve the legacy default namespace in that mode.
            member_count = await session.scalar(
                select(ProfileMember.id).where(ProfileMember.profile_id == profile_id).limit(1)
            )
            if member_count is None and profile_id == DEFAULT_PROFILE_ID:
                existing = set(
                    await session.scalars(
                        select(Person.id).where(Person.id.in_(requested), Person.active.is_(True))
                    )
                )
                if existing == requested:
                    return
            missing = sorted(requested - rows)
            raise ProfileError(
                f"one or more recipients are not members of profile {profile_id!r}: {missing}"
            )

    async def audience(self, profile_id: str) -> list[str]:
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(ProfileMember.person_id)
                    .join(Person, Person.id == ProfileMember.person_id)
                    .join(WeComIdentity, WeComIdentity.person_id == Person.id)
                    .where(
                        ProfileMember.profile_id == profile_id,
                        ProfileMember.enabled.is_(True),
                        Person.active.is_(True),
                        WeComIdentity.active.is_(True),
                    )
                    .distinct()
                    .order_by(ProfileMember.person_id)
                )
            )
            if rows:
                return rows
            if profile_id == DEFAULT_PROFILE_ID:
                return list(
                    await session.scalars(
                        select(Person.id)
                        .join(WeComIdentity, WeComIdentity.person_id == Person.id)
                        .where(Person.active.is_(True), WeComIdentity.active.is_(True))
                        .distinct()
                        .order_by(Person.id)
                    )
                )
            return []
