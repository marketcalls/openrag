import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_list_and_deactivate(
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

    headers = await auth(client, "a@acme.com")
    response = await client.get("/api/v1/users", headers=headers)
    emails = [user["email"] for user in response.json()]
    assert set(emails) == {"a@acme.com", "p@acme.com"}

    patch_response = await client.patch(
        f"/api/v1/users/{plain_user.id}",
        json={"active": False},
        headers=headers,
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["active"] is False

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "p@acme.com", "password": "pw123456"},
    )
    assert login_response.status_code == 401
