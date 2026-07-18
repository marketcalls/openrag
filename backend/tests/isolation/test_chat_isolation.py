"""Adversarial tenant and user isolation checks for the chat tier."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization, Role, UserRoleBinding
from openrag.modules.tenancy.service import seed_builtin_roles
from tests.api.test_chat_stream import (
    auth,
    chat_client,
    fake_streamer,
    make_model_and_chat,
    parse_sse,
)

__all__ = ["chat_client", "fake_streamer"]


@pytest.fixture
async def org_b_user(session: AsyncSession) -> User:
    organization = Organization(name="Rival Corp")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email="b@rival.example.com",
        password_hash=hash_password("pw123456"),
    )
    session.add(user)
    roles = await seed_builtin_roles(session, organization.id)
    await session.flush()
    session.add(
        UserRoleBinding(
            org_id=organization.id,
            user_id=user.id,
            role_id=roles["administrator"].id,
            created_by=user.id,
        )
    )
    await session.commit()
    return user


async def seeded_chat_with_message(
    client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    seeded_user: User,
    seeded_superadmin: User,
) -> tuple[str, str]:
    headers = await auth(client, seeded_user.email)
    chat_id = await make_model_and_chat(
        client,
        chat_env,
        seeded_superadmin,
        headers,
    )
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "secret question"},
        headers=headers,
    )
    done = next(
        data for event, data in parse_sse(response.text) if event == "done"
    )
    return chat_id, done["message_id"]


async def test_cross_org_chat_access_denied(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    seeded_user: User,
    seeded_superadmin: User,
    org_b_user: User,
) -> None:
    chat_id, message_id = await seeded_chat_with_message(
        chat_client,
        chat_env,
        seeded_user,
        seeded_superadmin,
    )
    rival_headers = await auth(chat_client, org_b_user.email)

    assert (
        await chat_client.get(
            f"/api/v1/chats/{chat_id}",
            headers=rival_headers,
        )
    ).status_code == 404
    assert (
        await chat_client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "leak?"},
            headers=rival_headers,
        )
    ).status_code == 404
    assert (
        await chat_client.post(
            f"/api/v1/messages/{message_id}/regenerate",
            headers=rival_headers,
        )
    ).status_code == 404
    assert (
        await chat_client.delete(
            f"/api/v1/chats/{chat_id}",
            headers=rival_headers,
        )
    ).status_code == 404
    assert (
        await chat_client.get("/api/v1/chats", headers=rival_headers)
    ).json() == []

    response = await chat_client.post(
        "/api/v1/chats",
        json={"workspace_id": str(chat_env["workspace"].id)},
        headers=rival_headers,
    )
    assert response.status_code == 404


async def test_same_org_users_have_private_chats(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    chat_id, message_id = await seeded_chat_with_message(
        chat_client,
        chat_env,
        seeded_user,
        seeded_superadmin,
    )
    peer = User(
        org_id=seeded_user.org_id,
        email="peer@acme.com",
        password_hash=hash_password("pw123456"),
    )
    session.add(peer)
    await session.flush()
    user_role = (
        await session.execute(
            select(Role).where(
                Role.org_id == seeded_user.org_id,
                Role.key == "user",
            )
        )
    ).scalar_one()
    session.add(
        UserRoleBinding(
            org_id=seeded_user.org_id,
            user_id=peer.id,
            role_id=user_role.id,
            created_by=seeded_user.id,
        )
    )
    await session.commit()
    peer_headers = await auth(chat_client, peer.email)

    assert (
        await chat_client.get(
            f"/api/v1/chats/{chat_id}",
            headers=peer_headers,
        )
    ).status_code == 404
    assert (
        await chat_client.post(
            f"/api/v1/messages/{message_id}/regenerate",
            headers=peer_headers,
        )
    ).status_code == 404
    assert (
        await chat_client.get("/api/v1/chats", headers=peer_headers)
    ).json() == []

    response = await chat_client.post(
        "/api/v1/chats",
        json={"workspace_id": str(chat_env["workspace"].id)},
        headers=peer_headers,
    )
    assert response.status_code == 404
