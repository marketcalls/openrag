from collections.abc import AsyncIterator
from uuid import UUID

from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.evaluations.executor import ProductionEvaluationExecutor
from openrag.modules.retrieval.service import (
    RetrievalResult,
    RetrievedChunk,
    RetrievedEvidence,
)

ORG_ID = UUID("550e8400-e29b-41d4-a716-446655440001")
WORKSPACE_ID = UUID("550e8400-e29b-41d4-a716-446655440002")
SPAN_A = UUID("550e8400-e29b-41d4-a716-446655440010")
SPAN_B = UUID("550e8400-e29b-41d4-a716-446655440011")


def evidence(identifier: UUID, marker: int) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=UUID(f"550e8400-e29b-41d4-a716-{marker:012d}"),
        document_version_id=UUID(f"650e8400-e29b-41d4-a716-{marker:012d}"),
        evidence_span_id=identifier,
        document_name=f"Manual {marker}.pdf",
        version_label="Approved 2",
        section_path=("Operating limits",),
        locator_kind="page",
        locator_label=str(marker),
        page_number=marker,
        chunk_ref=str(identifier),
        content_hash="a" * 64,
        text=f"Approved limit evidence {marker}",
        chunk_index=marker - 1,
        dense_score=0.9,
        sparse_score=0.8,
        fused_score=0.88,
    )


class Streamer:
    def __init__(self, items: list[LLMDelta | LLMUsage]) -> None:
        self.items = items
        self.messages: list[list[dict[str, str]]] = []

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        del model
        self.messages.append(messages)
        for item in self.items:
            yield item


class Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_executor_uses_authorized_retrieval_and_maps_citation_markers() -> None:
    retrieved = (evidence(SPAN_A, 1), evidence(SPAN_B, 2))
    calls: list[tuple[object, object, UUID, str, int]] = []

    async def retriever(
        session: object,
        context: object,
        workspace_id: UUID,
        query: str,
        top_k: int = 8,
    ) -> RetrievalResult:
        calls.append((session, context, workspace_id, query, top_k))
        return RetrievalResult(
            chunks=[
                RetrievedChunk(
                    item.document_id,
                    item.page_number,
                    item.chunk_index,
                    item.text,
                    item.fused_score,
                )
                for item in retrieved
            ],
            no_answer=False,
            evidence=retrieved,
        )

    streamer = Streamer([LLMDelta("The approved limit is documented [2]."), LLMUsage(420, 35)])
    executor = ProductionEvaluationExecutor(retriever=retriever)

    session = Session()
    context = object()
    observation = await executor.evaluate(
        session=session,  # type: ignore[arg-type]
        context=context,  # type: ignore[arg-type]
        workspace_id=WORKSPACE_ID,
        question="What is the approved limit?",
        model_name="openrag-answer-model",
        streamer=streamer,
    )

    assert calls == [
        (session, context, WORKSPACE_ID, "What is the approved limit?", 8)
    ]
    assert observation.retrieved_evidence_ids == (SPAN_A, SPAN_B)
    assert observation.cited_evidence_ids == (SPAN_B,)
    assert observation.did_refuse is False
    assert observation.prompt_tokens == 420
    assert observation.completion_tokens == 35
    assert observation.answer_digest is not None
    assert session.rollbacks == 1


async def test_judge_failure_does_not_erase_deterministic_observation() -> None:
    retrieved = (evidence(SPAN_A, 1),)

    async def retriever(*_args: object, **_kwargs: object) -> RetrievalResult:
        return RetrievalResult(
            chunks=[RetrievedChunk(retrieved[0].document_id, 1, 0, retrieved[0].text, 0.9)],
            no_answer=False,
            evidence=retrieved,
        )

    answer_streamer = Streamer([LLMDelta("Supported answer [1]."), LLMUsage(100, 10)])
    broken_judge = Streamer([LLMDelta("not-json")])
    executor = ProductionEvaluationExecutor(retriever=retriever)

    observation = await executor.evaluate(
        session=Session(),  # type: ignore[arg-type]
        context=object(),  # type: ignore[arg-type]
        workspace_id=WORKSPACE_ID,
        question="What is approved?",
        model_name="answer",
        streamer=answer_streamer,
        judge_model_name="judge",
        judge_streamer=broken_judge,
    )

    assert observation.cited_evidence_ids == (SPAN_A,)
    assert observation.answer_relevance is None
