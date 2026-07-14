import base64
import hashlib

from app.domain.clock import Clock
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import Secret
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SecretStore:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], clock: Clock, master_key: str
    ) -> None:
        key = base64.urlsafe_b64encode(hashlib.sha256(master_key.encode()).digest())
        self._cipher = Fernet(key)
        self._factory = factory
        self._clock = clock

    async def put(self, scope_type: str, scope_id: str, name: str, value: str) -> None:
        now = self._clock.now()
        ciphertext = self._cipher.encrypt(value.encode())
        async with self._factory() as session, session.begin():
            record = await session.scalar(
                select(Secret).where(
                    Secret.scope_type == scope_type,
                    Secret.scope_id == scope_id,
                    Secret.name == name,
                )
            )
            if record is None:
                session.add(
                    Secret(
                        id=new_id("secret"),
                        scope_type=scope_type,
                        scope_id=scope_id,
                        name=name,
                        ciphertext=ciphertext,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                record.ciphertext = ciphertext
                record.updated_at = now

    async def get(self, scope_type: str, scope_id: str, name: str) -> str | None:
        async with self._factory() as session:
            record = await session.scalar(
                select(Secret).where(
                    Secret.scope_type == scope_type,
                    Secret.scope_id == scope_id,
                    Secret.name == name,
                )
            )
        if record is None:
            return None
        try:
            return self._cipher.decrypt(record.ciphertext).decode()
        except InvalidToken as exc:
            raise RuntimeError("stored secret cannot be decrypted with the active key") from exc

    async def configured(self, scope_type: str, scope_id: str, name: str) -> bool:
        async with self._factory() as session:
            return (
                await session.scalar(
                    select(Secret.id).where(
                        Secret.scope_type == scope_type,
                        Secret.scope_id == scope_id,
                        Secret.name == name,
                    )
                )
                is not None
            )

    async def delete(self, scope_type: str, scope_id: str, name: str) -> bool:
        async with self._factory() as session, session.begin():
            record = await session.scalar(
                select(Secret).where(
                    Secret.scope_type == scope_type,
                    Secret.scope_id == scope_id,
                    Secret.name == name,
                )
            )
            if record is None:
                return False
            await session.delete(record)
            return True
