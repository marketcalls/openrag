"""Idempotent first-run bootstrap for the platform superadmin.

Usage: OPENRAG_BOOTSTRAP_EMAIL=... OPENRAG_BOOTSTRAP_PASSWORD=... \
uv run python -m openrag.bootstrap
"""

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization


async def bootstrap_superadmin(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    password: str,
) -> bool:
    async with session_factory() as session:
        existing = (
            await session.execute(select(User).where(User.role == "superadmin"))
        ).scalar_one_or_none()
        if existing is not None:
            return False

        organization = Organization(name="Platform")
        session.add(organization)
        await session.flush()
        session.add(
            User(
                org_id=organization.id,
                email=email,
                password_hash=hash_password(password),
                role="superadmin",
            )
        )
        await session.commit()
        return True


def main() -> None:
    email = os.environ.get("OPENRAG_BOOTSTRAP_EMAIL")
    password = os.environ.get("OPENRAG_BOOTSTRAP_PASSWORD")
    if not email or not password:
        print(
            "Set OPENRAG_BOOTSTRAP_EMAIL and OPENRAG_BOOTSTRAP_PASSWORD",
            file=sys.stderr,
        )
        raise SystemExit(2)

    settings = get_settings()
    factory = build_session_factory(build_engine(settings.database_url))
    created = asyncio.run(
        bootstrap_superadmin(factory, email=email, password=password)
    )
    print("superadmin created" if created else "superadmin already exists")


if __name__ == "__main__":
    main()
