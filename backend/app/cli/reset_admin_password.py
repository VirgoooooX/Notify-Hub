from __future__ import annotations

import argparse
import asyncio
from getpass import getpass

from sqlalchemy import select, update

from app.config import Settings
from app.domain.clock import SystemClock
from app.infrastructure.database.models import Admin, RefreshSession
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.security.tokens import hash_password


def read_password() -> str:
    password = getpass("新管理员密码（至少 12 个字符）: ")
    if len(password) < 12:
        raise ValueError("密码至少需要 12 个字符")
    confirmation = getpass("再次输入新密码: ")
    if password != confirmation:
        raise ValueError("两次输入的密码不一致")
    return password


async def reset_password(username: str, password: str) -> None:
    settings = Settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    now = SystemClock().now()
    try:
        async with factory() as session, session.begin():
            admin = await session.scalar(select(Admin).where(Admin.username == username))
            if admin is None:
                raise ValueError(f"管理员 {username!r} 不存在")
            admin.password_hash = hash_password(password)
            admin.updated_at = now
            await session.execute(
                update(RefreshSession)
                .where(
                    RefreshSession.admin_id == admin.id,
                    RefreshSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="安全重置本地 Notify Hub 管理员密码")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    try:
        password = read_password()
        asyncio.run(reset_password(args.username, password))
    except ValueError as exc:
        parser.exit(1, f"重置失败：{exc}\n")
    print(f"管理员 {args.username!r} 的密码已重置，现有刷新会话已撤销。")


if __name__ == "__main__":
    main()
