from collections.abc import Sequence
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.api.app import create_app
from openrag.core.config import Settings, get_settings
from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.chat.service import NO_ANSWER_TEXT
from openrag.modules.retrieval.service import (
    CitationEvidenceIdentity,
    RetrievalResult,
    RetrievedChunk,
)
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import FakeStreamer


class SequenceRetriever:
    def __init__(self, results: Sequence[RetrievalResult]) -> None:
        self.results = list(results)

    async def __call__(
        self,
        session: AsyncSession,
        context: object,
        workspace_id: object,
        query: str,
        top_k: int = 8,
    ) -> RetrievalResult:
        await session.rollback()
        return self.results.pop(0)


class RecordingCitationBackfiller:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[tuple[CitationEvidenceIdentity, ...]] = []

    async def __call__(
        self,
        session: AsyncSession,
        context: object,
        workspace_id: object,
        identities: Sequence[CitationEvidenceIdentity],
        top_k: int = 8,
    ) -> RetrievalResult:
        self.calls.append(tuple(identities))
        return self.result


async def test_weak_table_format_followup_backfills_nearest_grounded_citations(
    engine: AsyncEngine,
    redis_client: Redis,
    test_settings: Settings,
    chat_env: dict[str, Any],
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    document = chat_env["document"]
    first = RetrievalResult(
        chunks=[
            RetrievedChunk(
                document_id=document.id,
                page=1,
                chunk_index=0,
                text="Taxable amount is 500000 and IGST is 90000.",
                score=0.91,
            )
        ],
        no_answer=False,
    )
    weak = RetrievalResult(
        chunks=[
            RetrievedChunk(
                document_id=document.id,
                page=1,
                chunk_index=4,
                text="Weak unrelated candidate.",
                score=0.01,
            )
        ],
        no_answer=True,
    )
    backfilled = RetrievalResult(
        chunks=[
            RetrievedChunk(
                document_id=document.id,
                page=1,
                chunk_index=0,
                text="Taxable amount is 500000 and IGST is 90000.",
                score=0.0,
            )
        ],
        no_answer=False,
    )
    backfiller = RecordingCitationBackfiller(backfilled)
    streamer = FakeStreamer(
        deltas=["| Field | Amount |\n|---|---:|\n| IGST | 90000 | [1]"]
    )
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        retriever=SequenceRetriever([first, weak, weak]),
        citation_backfiller=backfiller,
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
            session,
        )
        first_response = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "summarize the invoice tax details"},
            headers=headers,
        )
        assert first_response.status_code == 200

        response = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "provide the above in table format"},
            headers=headers,
        )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert len(backfiller.calls) == 1
    assert backfiller.calls[0][0].chunk_ref == f"{document.id}:1:0"
    assert next(data for event, data in events if event == "done")["no_answer"] is False
    text = "".join(data["delta"] for event, data in events if event == "token")
    assert NO_ANSWER_TEXT not in text
    assert "| Field | Amount |" in text
    citations = next(data for event, data in events if event == "citations")["citations"]
    assert citations[0]["chunk_ref"] == f"{document.id}:1:0"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        headers = await auth(client, seeded_user.email)
        acknowledgement = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "thanks"},
            headers=headers,
        )
        assert acknowledgement.status_code == 200
        after_uncited = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "provide the above in table format"},
            headers=headers,
        )

    assert after_uncited.status_code == 200
    after_uncited_events = parse_sse(after_uncited.text)
    assert len(backfiller.calls) == 1
    assert next(
        data for event, data in after_uncited_events if event == "done"
    )["no_answer"] is True
