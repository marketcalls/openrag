"""Idempotent first-run bootstrap for the platform superadmin.

Usage: OPENRAG_BOOTSTRAP_EMAIL=... OPENRAG_BOOTSTRAP_PASSWORD=... \
uv run python -m openrag.bootstrap
"""

import asyncio
import os
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import get_settings
from openrag.core.db import build_configured_engine, build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.secrets.crypto import ensure_kek
from openrag.modules.tenancy.models import Organization
from openrag.modules.tenancy.service import seed_builtin_roles

_BOOTSTRAP_ADVISORY_LOCK_ID = 0x4F50454E524147


async def bootstrap_superadmin(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    password: str,
) -> bool:
    async with session_factory() as session:
        # Serialize all bootstrap attempts before observing privileged state. A
        # transaction-scoped lock is released only by commit/rollback/session close.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _BOOTSTRAP_ADVISORY_LOCK_ID},
        )
        existing = (
            await session.execute(
                select(User).where(User.is_platform_superadmin.is_(True))
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False

        organization = (
            await session.execute(
                select(Organization).where(Organization.name == "Platform")
            )
        ).scalar_one_or_none()
        if organization is None:
            organization = Organization(name="Platform")
            session.add(organization)
            await session.flush()
        await seed_builtin_roles(session, organization.id)
        session.add(
            User(
                org_id=organization.id,
                email=email,
                password_hash=hash_password(password),
                is_platform_superadmin=True,
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
    ensure_kek(settings.kek_file)
    print(f"KEK ready at {settings.kek_file}")
    factory = build_session_factory(build_configured_engine(settings))
    created = asyncio.run(
        bootstrap_superadmin(factory, email=email, password=password)
    )
    print("superadmin created" if created else "superadmin already exists")


if __name__ == "__main__":
    main()
