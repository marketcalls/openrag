from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from tests.api.test_chat_stream import (
    auth,
    chat_client,
    fake_streamer,
    make_model_and_chat,
)

__all__ = ["chat_client", "fake_streamer"]


async def test_history_crud_and_tree_shape(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(
        chat_client,
        chat_env,
        seeded_superadmin,
        headers,
    )
    await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "version one?"},
        headers=headers,
    )
    await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "version two?", "parent_message_id": None},
        headers=headers,
    )

    response = await chat_client.get(
        f"/api/v1/chats/{chat_id}",
        headers=headers,
    )
    assert response.status_code == 200
    tree = response.json()
    assert tree["id"] == chat_id
    roots = tree["messages"]
    assert [message["sibling_index"] for message in roots] == [0, 1]
    assert [message["content"] for message in roots] == [
        "version one?",
        "version two?",
    ]
    for root in roots:
        assert root["role"] == "user"
        assert root["parent_message_id"] is None
        assert len(root["children"]) == 1
        child = root["children"][0]
        assert child["role"] == "assistant"
        assert child["parent_message_id"] == root["id"]
        assert child["children"] == []
        assert [citation["marker"] for citation in child["citations"]] == [1]
        source = child["citations"][0]
        assert source["document_name"]
        assert source["version_label"] == "Legacy 1"
        assert source["section_label"] == "Legacy import"
        assert source["section_path"] == ["Legacy import"]
        assert source["locator_kind"] == "page"
        assert source["locator_label"] == str(source["page"])
        assert source["verification_state"] == "legacy_unverified"
        assert "content_hash" not in source

    response = await chat_client.patch(
        f"/api/v1/chats/{chat_id}",
        json={"title": "Renamed"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert response.json()["created_at"]
    assert response.json()["updated_at"]

    listing = await chat_client.get("/api/v1/chats", headers=headers)
    assert listing.status_code == 200
    assert [chat["title"] for chat in listing.json()] == ["Renamed"]

    deleted = await chat_client.delete(
        f"/api/v1/chats/{chat_id}",
        headers=headers,
    )
    assert deleted.status_code == 204
    assert (
        await chat_client.get(
            f"/api/v1/chats/{chat_id}",
            headers=headers,
        )
    ).status_code == 404
