import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_admin_creates_workspace_and_adds_member(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    plain_user = User(
        org_id=seeded_user.org_id,
        email="p@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(plain_user)
    await session.commit()

    admin_headers = await auth(client, "a@acme.com")
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Finance"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    workspace_id = response.json()["id"]

    user_headers = await auth(client, "p@acme.com")
    assert (
        await client.get("/api/v1/workspaces", headers=user_headers)
    ).json() == []
    assert (
        await client.post(
            "/api/v1/workspaces",
            json={"name": "X"},
            headers=user_headers,
        )
    ).status_code == 403

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"user_id": str(plain_user.id)},
        headers=admin_headers,
    )
    assert response.status_code == 204
    workspaces = (
        await client.get("/api/v1/workspaces", headers=user_headers)
    ).json()
    assert [workspace["name"] for workspace in workspaces] == ["Finance"]
