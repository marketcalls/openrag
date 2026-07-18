from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from openrag.core.errors import AuthenticationError
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS

_ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    org_id: UUID
    is_platform_superadmin: bool
    permissions: frozenset[str]


def issue_access_token(
    *,
    user_id: UUID,
    org_id: UUID,
    is_platform_superadmin: bool,
    permissions: frozenset[str],
    signing_key: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "platform_superadmin": is_platform_superadmin,
        "permissions": sorted(permissions),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, signing_key, algorithm=_ALGORITHM)


def decode_access_token(token: str, signing_key: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=[_ALGORITHM],
            options={
                "require": [
                    "sub",
                    "org",
                    "platform_superadmin",
                    "permissions",
                    "iat",
                    "exp",
                ]
            },
        )
        subject = payload["sub"]
        organization = payload["org"]
        platform_superadmin = payload["platform_superadmin"]
        permissions = payload["permissions"]
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        if type(subject) is not str or type(organization) is not str:
            raise TypeError("sub and org must be strings")
        if type(platform_superadmin) is not bool:  # bool must not accept int
            raise TypeError("platform_superadmin must be boolean")
        if not isinstance(permissions, list) or any(
            not isinstance(permission, str) or permission not in ALL_PERMISSIONS
            for permission in permissions
        ):
            raise TypeError("permissions must use the closed vocabulary")
        if type(issued_at) is not int or type(expires_at) is not int:
            raise TypeError("iat and exp must be integers")
        if expires_at <= issued_at:
            raise ValueError("exp must follow iat")
        return AccessClaims(
            user_id=UUID(subject),
            org_id=UUID(organization),
            is_platform_superadmin=platform_superadmin,
            permissions=frozenset(permissions),
        )
    except (
        jwt.InvalidTokenError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise AuthenticationError("invalid or expired token") from exc
