from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import NotFoundError, SecretsError
from openrag.modules.audit.models import AuditEvent
from openrag.modules.secrets.crypto import ensure_kek
from openrag.modules.secrets.models import Secret
from openrag.modules.secrets.service import (
    _get_secret_decrypted,
    delete_secret,
    list_secrets,
    set_secret,
)


@pytest.fixture
def secret_settings(tmp_path: Path) -> Settings:
    key_file = tmp_path / "kek"
    ensure_kek(str(key_file))
    return Settings(_env_file=None, kek_file=str(key_file))


async def test_set_get_roundtrip_updates_last_used(
    session: AsyncSession,
    secret_settings: Settings,
) -> None:
    await set_secret(
        session,
        actor_id=None,
        name="model:x",
        value="sk-live-1234",
        settings=secret_settings,
    )
    row = (
        await session.execute(select(Secret).where(Secret.name == "model:x"))
    ).scalar_one()

    assert row.last_used_at is None
    assert b"sk-live-1234" not in row.ciphertext
    assert (
        await _get_secret_decrypted(
            session,
            name="model:x",
            settings=secret_settings,
        )
        == "sk-live-1234"
    )
    await session.refresh(row)
    assert row.last_used_at is not None


async def test_set_secret_upserts_and_audits(
    session: AsyncSession,
    secret_settings: Settings,
) -> None:
    await set_secret(
        session,
        actor_id=None,
        name="provider-key",
        value="one",
        settings=secret_settings,
    )
    await set_secret(
        session,
        actor_id=None,
        name="provider-key",
        value="two",
        settings=secret_settings,
    )

    assert len(await list_secrets(session)) == 1
    assert (
        await _get_secret_decrypted(
            session,
            name="provider-key",
            settings=secret_settings,
        )
        == "two"
    )
    actions = [
        event.action
        for event in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert actions == ["secret.written", "secret.written"]


async def test_unknown_secret_raises(
    session: AsyncSession,
    secret_settings: Settings,
) -> None:
    with pytest.raises(NotFoundError):
        await _get_secret_decrypted(
            session,
            name="ghost",
            settings=secret_settings,
        )


async def test_wrong_kek_fails_closed(
    session: AsyncSession,
    secret_settings: Settings,
    tmp_path: Path,
) -> None:
    await set_secret(
        session,
        actor_id=None,
        name="provider-key",
        value="value",
        settings=secret_settings,
    )
    other_key = tmp_path / "other-kek"
    ensure_kek(str(other_key))
    wrong_settings = Settings(_env_file=None, kek_file=str(other_key))

    with pytest.raises(SecretsError):
        await _get_secret_decrypted(
            session,
            name="provider-key",
            settings=wrong_settings,
        )


async def test_delete_secret_is_idempotent(
    session: AsyncSession,
    secret_settings: Settings,
) -> None:
    await set_secret(
        session,
        actor_id=None,
        name="provider-key",
        value="value",
        settings=secret_settings,
    )

    await delete_secret(session, name="provider-key")
    await delete_secret(session, name="provider-key")

    assert await list_secrets(session) == []
