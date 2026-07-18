"""Write-only secret management with one sanctioned plaintext read path."""

from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.secrets import crypto
from openrag.modules.secrets.models import Secret


async def set_secret(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    name: str,
    value: str,
    settings: Settings,
    commit: bool = True,
) -> Secret:
    key = crypto.load_kek(settings.kek_file)
    nonce, ciphertext = crypto.encrypt(key, value)
    row = (
        await session.execute(select(Secret).where(Secret.name == name))
    ).scalar_one_or_none()
    if row is None:
        row = Secret(
            name=name,
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=crypto.KEY_VERSION,
            fingerprint=crypto.fingerprint(value),
        )
        session.add(row)
    else:
        row.ciphertext = ciphertext
        row.nonce = nonce
        row.key_version = crypto.KEY_VERSION
        row.fingerprint = crypto.fingerprint(value)
    await record_audit(
        session,
        org_id=None,
        actor_id=actor_id,
        action="secret.written",
        target_type="secret",
        target_id=name,
    )
    if commit:
        await session.commit()
    return row


async def list_secrets(session: AsyncSession) -> list[Secret]:
    return list(
        (await session.execute(select(Secret).order_by(Secret.name))).scalars()
    )


async def delete_secret(session: AsyncSession, *, name: str) -> None:
    await session.execute(sa_delete(Secret).where(Secret.name == name))
    await session.commit()


async def _get_secret_decrypted(
    session: AsyncSession,
    *,
    name: str,
    settings: Settings,
) -> str:
    row = (
        await session.execute(select(Secret).where(Secret.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"secret {name!r} not set")
    value = crypto.decrypt(
        crypto.load_kek(settings.kek_file),
        row.nonce,
        row.ciphertext,
    )
    row.last_used_at = naive_utc()
    await session.commit()
    return value
