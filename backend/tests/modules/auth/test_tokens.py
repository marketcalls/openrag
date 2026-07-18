from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.errors import AuthenticationError
from openrag.modules.auth.tokens import decode_access_token, issue_access_token


async def test_signing_key_persisted(session: AsyncSession) -> None:
    first = await get_or_create_signing_key(session)
    second = await get_or_create_signing_key(session)
    assert first == second
    assert len(first) >= 43


def test_token_roundtrip() -> None:
    user_id, org_id = uuid4(), uuid4()
    token = issue_access_token(
        user_id=user_id,
        org_id=org_id,
        role="admin",
        signing_key="k" * 43,
        ttl_seconds=900,
    )
    claims = decode_access_token(token, "k" * 43)
    assert claims.user_id == user_id
    assert claims.org_id == org_id
    assert claims.role == "admin"


def test_bad_signature_rejected() -> None:
    token = issue_access_token(
        user_id=uuid4(),
        org_id=uuid4(),
        role="user",
        signing_key="k" * 43,
        ttl_seconds=900,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, "x" * 43)


def test_expired_rejected() -> None:
    token = issue_access_token(
        user_id=uuid4(),
        org_id=uuid4(),
        role="user",
        signing_key="k" * 43,
        ttl_seconds=-1,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, "k" * 43)
