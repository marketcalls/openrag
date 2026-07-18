from typing import Annotated

import httpx
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_role,
)


def wire_probe(app: FastAPI) -> None:
    @app.get("/probe/me")
    async def me(
        ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> dict[str, str]:
        return {"role": ctx.role, "org_id": str(ctx.org_id)}

    @app.get("/probe/admin", dependencies=[Depends(require_role("admin"))])
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}


async def login_token(
    client: httpx.AsyncClient,
    email: str,
) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return str(response.json()["access_token"])


async def test_me_requires_token(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    assert (await client.get("/probe/me")).status_code == 401
    token = await login_token(client, "a@acme.com")
    response = await client.get(
        "/probe/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_role_guard(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    plain_user = User(
        org_id=seeded_user.org_id,
        email="p@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(plain_user)
    await session.commit()

    admin_token = await login_token(client, "a@acme.com")
    user_token = await login_token(client, "p@acme.com")
    assert (
        await client.get(
            "/probe/admin",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).status_code == 200
    assert (
        await client.get(
            "/probe/admin",
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 403
