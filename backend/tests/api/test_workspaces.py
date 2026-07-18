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


async def test_admin_lists_workspace_members(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    member = User(
        org_id=seeded_user.org_id,
        email="member@acme.com",
        password_hash=seeded_user.password_hash,
    )
    session.add(member)
    await session.commit()
    headers = await auth(client, "a@acme.com")
    workspace = await client.post(
        "/api/v1/workspaces",
        json={"name": "Member list"},
        headers=headers,
    )
    workspace_id = workspace.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"user_id": str(member.id)},
        headers=headers,
    )

    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "user_id": str(member.id),
            "email": "member@acme.com",
            "role": "member",
        }
    ]
