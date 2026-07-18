from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.app_settings import get_or_create_signing_key
from openrag.core.errors import AuthenticationError
from openrag.modules.auth.tokens import decode_access_token, issue_access_token


def signed_claims(**overrides: object) -> str:
    now = int(datetime.now(UTC).timestamp())
    payload: dict[str, object] = {
        "sub": str(uuid4()),
        "org": str(uuid4()),
        "platform_superadmin": False,
        "permissions": ["chat.use"],
        "iat": now,
        "exp": now + 300,
    }
    payload.update(overrides)
    return jwt.encode(payload, "k" * 43, algorithm="HS256")


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
        is_platform_superadmin=False,
        permissions=frozenset({"role.manage"}),
        signing_key="k" * 43,
        ttl_seconds=900,
    )
    claims = decode_access_token(token, "k" * 43)
    assert claims.user_id == user_id
    assert claims.org_id == org_id
    assert claims.is_platform_superadmin is False
    assert claims.permissions == frozenset({"role.manage"})


def test_bad_signature_rejected() -> None:
    token = issue_access_token(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_superadmin=False,
        permissions=frozenset({"chat.use"}),
        signing_key="k" * 43,
        ttl_seconds=900,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, "x" * 43)


def test_expired_rejected() -> None:
    token = issue_access_token(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_superadmin=False,
        permissions=frozenset({"chat.use"}),
        signing_key="k" * 43,
        ttl_seconds=-1,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, "k" * 43)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("sub", 123),
        ("sub", True),
        ("org", 123),
        ("org", False),
        ("platform_superadmin", 1),
        ("platform_superadmin", "false"),
        ("permissions", "chat.use"),
        ("permissions", ["unknown.use"]),
        ("permissions", [True]),
    ],
)
def test_malformed_signed_claim_shapes_are_rejected(
    claim: str,
    value: object,
) -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token(signed_claims(**{claim: value}), "k" * 43)


def test_non_allowlisted_algorithm_is_rejected() -> None:
    now = int(datetime.now(UTC).timestamp())
    signing_key = "k" * 48
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "org": str(uuid4()),
            "platform_superadmin": False,
            "permissions": ["chat.use"],
            "iat": now,
            "exp": now + 300,
        },
        signing_key,
        algorithm="HS384",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, signing_key)


@pytest.mark.parametrize(
    "missing_claim",
    ["sub", "org", "platform_superadmin", "permissions", "iat", "exp"],
)
def test_required_claims_cannot_be_omitted(missing_claim: str) -> None:
    now = int(datetime.now(UTC).timestamp())
    payload: dict[str, object] = {
        "sub": str(uuid4()),
        "org": str(uuid4()),
        "platform_superadmin": False,
        "permissions": ["chat.use"],
        "iat": now,
        "exp": now + 300,
    }
    del payload[missing_claim]
    token = jwt.encode(payload, "k" * 43, algorithm="HS256")

    with pytest.raises(AuthenticationError):
        decode_access_token(token, "k" * 43)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iat", True),
        ("iat", "100"),
        ("iat", 10.5),
        ("exp", False),
        ("exp", "200"),
        ("exp", 20.5),
    ],
)
def test_temporal_claims_must_be_non_boolean_integers(
    claim: str,
    value: object,
) -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token(signed_claims(**{claim: value}), "k" * 43)


def test_expiry_must_follow_issued_at() -> None:
    now = int(datetime.now(UTC).timestamp())
    with pytest.raises(AuthenticationError):
        decode_access_token(
            signed_claims(iat=now + 100, exp=now + 50),
            "k" * 43,
        )
