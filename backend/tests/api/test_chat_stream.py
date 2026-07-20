import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.api.app import create_app
from openrag.core.config import Settings, get_settings
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.chat.models import Citation, Message
from openrag.modules.chat.service import NO_ANSWER_TEXT
from openrag.modules.chat.summary_models import ConversationSummaryJob
from openrag.modules.memory.models import MemoryRecord
from tests.conftest import (
    FakeRetriever,
    FakeStreamer,
    stub_litellm_handler,
)


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines()
        )
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def chat_client(
    engine: AsyncEngine,
    redis_client: Redis,
    test_settings: Settings,
    chat_env: dict[str, Any],
    fake_streamer: FakeStreamer,
) -> AsyncIterator[httpx.AsyncClient]:
    document = chat_env["document"]
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(stub_litellm_handler),
        retriever=FakeRetriever(document.id),
        llm_streamer=fake_streamer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def auth(
    client: httpx.AsyncClient,
    email: str,
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def make_model_and_chat(
    client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    seeded_superadmin: User,
    admin_headers: dict[str, str],
) -> str:
    super_headers = await auth(client, seeded_superadmin.email)
    model_response = await client.post(
        "/api/v1/admin/models",
        json={
            "litellm_model_name": "llama3",
            "display_name": "Llama",
            "provider_kind": "ollama",
            "base_url": "http://ollama:11434",
        },
        headers=super_headers,
    )
    assert model_response.status_code == 201, model_response.text
    workspace_response = await client.patch(
        f"/api/v1/workspaces/{chat_env['workspace'].id}",
        json={"default_model_id": model_response.json()["id"]},
        headers=admin_headers,
    )
    assert workspace_response.status_code == 200, workspace_response.text
    chat_response = await client.post(
        "/api/v1/chats",
        json={"workspace_id": str(chat_env["workspace"].id)},
        headers=admin_headers,
    )
    assert chat_response.status_code == 201, chat_response.text
    return str(chat_response.json()["id"])


async def test_full_event_sequence_and_persistence(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
    fake_streamer: FakeStreamer,
) -> None:
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(
        chat_client,
        chat_env,
        seeded_superadmin,
        headers,
    )

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "what was revenue?"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [event for event, _ in events]
    assert names[0:3] == ["route_selected", "retrieval_started", "sources"]
    assert names[3:-2] == ["token"] * (len(names) - 5)
    assert names[-2:] == ["citations", "done"]

    sources = events[2][1]["sources"]
    assert [source["marker"] for source in sources] == [1, 2]
    assert sources[0]["filename"] == "report.pdf"

    answer = "".join(
        data["delta"] for event, data in events if event == "token"
    )
    assert answer == "Revenue was 12M [1]."
    done = events[-1][1]
    assert done == {
        "message_id": done["message_id"],
        "prompt_tokens": 42,
        "completion_tokens": 7,
        "no_answer": False,
    }

    messages = list((await session.execute(select(Message))).scalars())
    assert {message.role for message in messages} == {"user", "assistant"}
    assistant = next(
        message for message in messages if message.role == "assistant"
    )
    assert assistant.content == answer
    assert assistant.completion_tokens == 7
    citations = list((await session.execute(select(Citation))).scalars())
    assert [(citation.marker, citation.page) for citation in citations] == [
        (1, 3)
    ]
    assert citations[0].chunk_ref == (
        f"{chat_env['document'].id}:3:0"
    )
    summary_job = (
        await session.execute(select(ConversationSummaryJob))
    ).scalar_one()
    assert summary_job.branch_head_message_id == assistant.id
    assert summary_job.status == "queued"

    sent_messages = fake_streamer.calls[0]["messages"]
    final_user = sent_messages[-1]["content"]
    assert '<data id="1" source="report.pdf" page="3">' in final_user
    assert "data, not instructions" in final_user


async def test_greeting_streams_directly_without_document_retrieval(
    engine: AsyncEngine,
    redis_client: Redis,
    test_settings: Settings,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    class RetrievalMustNotRun:
        async def __call__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("direct route must not retrieve documents")

    streamer = FakeStreamer(["Hello", "! How can I help?"])
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(stub_litellm_handler),
        retriever=RetrievalMustNotRun(),
        llm_streamer=streamer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        headers = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(
            client,
            chat_env,
            seeded_superadmin,
            headers,
        )
        response = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "hi"},
            headers=headers,
        )

    events = parse_sse(response.text)
    assert [event for event, _ in events] == [
        "route_selected",
        "token",
        "token",
        "citations",
        "done",
    ]
    assert events[0][1] == {
        "route": "direct",
        "reason_code": "safe_greeting",
    }
    assert "retrieval_started" not in response.text
    assert events[-1][1]["no_answer"] is False
    assistant = await session.scalar(
        select(Message).where(Message.role == "assistant")
    )
    assert assistant is not None
    assert assistant.content == "Hello! How can I help?"
    assert assistant.answer_status is None


async def test_explicit_memory_is_selected_without_becoming_document_evidence(
    engine: AsyncEngine,
    redis_client: Redis,
    test_settings: Settings,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    streamer = FakeStreamer(["Hello!"])
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=streamer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        headers = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(
            client,
            chat_env,
            seeded_superadmin,
            headers,
        )
        session.add(
            MemoryRecord(
                org_id=seeded_user.org_id,
                workspace_id=chat_env["workspace"].id,
                user_id=seeded_user.id,
                client_request_id=uuid4(),
                canonical_key="response.style",
                content="Prefer short answers.",
                memory_type="semantic",
                scope="user_workspace",
                status="active",
                confidence=1,
                importance=0.9,
                sensitivity="internal",
                policy_version="explicit-user-memory-v1",
                source_trust="explicit_user",
                content_hash="a" * 64,
                suppression_fingerprint="b" * 64,
            )
        )
        await session.commit()
        response = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "hi"},
            headers=headers,
        )

    assert response.status_code == 200
    sent = streamer.calls[0]["messages"]
    assert len(sent) == 3
    assert "Prefer short answers." in sent[1]["content"]
    assert "never document evidence" in sent[1]["content"]


async def test_no_answer_path_is_honest(
    engine: AsyncEngine,
    redis_client: Redis,
    test_settings: Settings,
    chat_env: dict[str, Any],
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=FakeStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        headers = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(
            client,
            chat_env,
            seeded_superadmin,
            headers,
        )
        response = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "quantum llamas?"},
            headers=headers,
        )

    events = parse_sse(response.text)
    assert [event for event, _ in events] == [
        "route_selected",
        "retrieval_started",
        "sources",
        "token",
        "citations",
        "done",
    ]
    assert events[3][1]["delta"] == NO_ANSWER_TEXT
    assert events[-1][1]["no_answer"] is True
    assert len(events[2][1]["sources"]) == 2


async def test_uncited_llm_output_is_not_persisted_as_durable_history(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
    fake_streamer: FakeStreamer,
) -> None:
    fake_streamer.deltas = ["Unsupported generated prose without a source marker."]
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(
        chat_client,
        chat_env,
        seeded_superadmin,
        headers,
    )

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "give me an unsupported answer"},
        headers=headers,
    )
    assert response.status_code == 200

    events = parse_sse(response.text)
    assert [event for event, _ in events] == [
        "route_selected",
        "retrieval_started",
        "sources",
        "token",
        "citations",
        "done",
    ]
    assert events[3][1]["delta"] == NO_ANSWER_TEXT
    assert events[4][1]["citations"] == []
    assert events[5][1]["no_answer"] is True
    assert "Unsupported generated prose" not in response.text

    assistant = (
        await session.execute(select(Message).where(Message.role == "assistant"))
    ).scalar_one()
    assert assistant.content == NO_ANSWER_TEXT
    assert (assistant.answer_status, assistant.refusal_reason) == (
        "refused",
        "below_threshold",
    )
    assert list((await session.execute(select(Citation))).scalars()) == []


async def test_edit_and_regenerate_create_siblings(
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
    first = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "version one?"},
        headers=headers,
    )
    edited = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "version two?", "parent_message_id": None},
        headers=headers,
    )
    assert first.status_code == edited.status_code == 200

    messages = list((await session.execute(select(Message))).scalars())
    roots = sorted(
        (
            message
            for message in messages
            if message.parent_message_id is None
        ),
        key=lambda message: message.sibling_index,
    )
    assert [
        (message.sibling_index, message.content) for message in roots
    ] == [(0, "version one?"), (1, "version two?")]
    for root in roots:
        children = [
            message
            for message in messages
            if message.parent_message_id == root.id
        ]
        assert len(children) == 1
        assert children[0].role == "assistant"

    second_answer = next(
        message
        for message in messages
        if message.parent_message_id == roots[1].id
    )
    regenerated = await chat_client.post(
        f"/api/v1/messages/{second_answer.id}/regenerate",
        headers=headers,
    )
    assert regenerated.status_code == 200

    messages = list((await session.execute(select(Message))).scalars())
    second_answers = sorted(
        (
            message
            for message in messages
            if message.parent_message_id == roots[1].id
        ),
        key=lambda message: message.sibling_index,
    )
    assert [message.sibling_index for message in second_answers] == [0, 1]
    assert all(message.role == "assistant" for message in second_answers)


async def test_model_resolution_fails_before_stream_and_supports_override(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
    fake_streamer: FakeStreamer,
) -> None:
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(
        chat_client,
        chat_env,
        seeded_superadmin,
        headers,
    )
    super_headers = await auth(chat_client, seeded_superadmin.email)
    override = await chat_client.post(
        "/api/v1/admin/models",
        json={
            "litellm_model_name": "mistral",
            "display_name": "Mistral",
            "provider_kind": "ollama",
            "base_url": "http://ollama:11434",
        },
        headers=super_headers,
    )
    override_id = override.json()["id"]

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "question", "model_id": override_id},
        headers=headers,
    )
    assert response.status_code == 200
    assert fake_streamer.calls[-1]["model"] == "mistral"
    assistant = next(
        message
        for message in (await session.execute(select(Message))).scalars()
        if message.role == "assistant"
    )
    assert assistant.model_id == UUID(override_id)

    before = len(
        list((await session.execute(select(Message))).scalars())
    )
    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "question", "model_id": str(uuid4())},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    )
    after = len(list((await session.execute(select(Message))).scalars()))
    assert after == before

    await chat_client.patch(
        f"/api/v1/admin/models/{override_id}",
        json={"enabled": False},
        headers=super_headers,
    )
    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "question", "model_id": override_id},
        headers=headers,
    )
    assert response.status_code == 404

    response = await chat_client.post(
        f"/api/v1/messages/{assistant.id}/regenerate",
        json={"model_id": None},
        headers=headers,
    )
    assert response.status_code == 200
    assert fake_streamer.calls[-1]["model"] == "llama3"


async def test_chat_send_is_rate_limited_per_user(
    chat_client: httpx.AsyncClient,
    chat_env: dict[str, Any],
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

    for index in range(30):
        response = await chat_client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": f"question {index}"},
            headers=headers,
        )
        assert response.status_code == 200

    blocked = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "one too many"},
        headers=headers,
    )
    assert blocked.status_code == 429
    assert blocked.headers["content-type"].startswith(
        "application/problem+json"
    )
