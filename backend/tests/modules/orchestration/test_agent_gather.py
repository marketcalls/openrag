from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.modules.orchestration.agent_gather import (
    AgentGatherCompleted,
    AgentGatherer,
)
from openrag.modules.orchestration.agent_loop import (
    AgentAction,
    AgentLoopProgress,
    AgentLoopState,
    AgentToolCall,
)
from openrag.modules.orchestration.model_gateway import ModelRuntime
from openrag.modules.orchestration.retrieval_tools import (
    AuthorizedToolEvidence,
    RetrievalToolExecutor,
)
from openrag.modules.orchestration.runtime import build_agent_gatherer_factory
from openrag.modules.retrieval.service import RetrievalResult, RetrievedEvidence
from openrag.modules.tenancy.context import TenantContext


def _evidence(*, score: float, text: str) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=uuid4(),
        document_version_id=uuid4(),
        evidence_span_id=uuid4(),
        document_name="Procedure.pdf",
        version_label="v4",
        section_path=("Response",),
        locator_kind="page",
        locator_label="4",
        page_number=4,
        chunk_ref="span",
        content_hash=uuid4().hex * 2,
        text=text,
        chunk_index=3,
        dense_score=score,
        sparse_score=None,
        fused_score=score,
    )


async def test_gather_streams_progress_and_returns_revalidated_merged_evidence() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    initial = _evidence(score=0.65, text="Initial weak evidence")
    expanded = _evidence(score=0.93, text="Expanded strong evidence")

    class Backend:
        async def search(self, query: str, metadata: object) -> tuple[AuthorizedToolEvidence, ...]:
            assert query == "emergency response details"
            assert metadata is None
            return (AuthorizedToolEvidence(org_id, workspace_id, expanded),)

        async def get_document(self, document_id: object) -> tuple[AuthorizedToolEvidence, ...]:
            raise AssertionError(document_id)

    async def planner(state: AgentLoopState) -> AgentAction:
        if len(state.observations) == 1:
            return AgentAction.tool(
                AgentToolCall(name="search", query="emergency response details")
            )
        return AgentAction.finish()

    gatherer = AgentGatherer(
        planner,
        RetrievalToolExecutor(Backend(), org_id=org_id, workspace_id=workspace_id),
    )
    initial_result = RetrievalResult(chunks=[], no_answer=True, evidence=(initial,))

    events = [
        event
        async for event in gatherer.stream(
            query="What is the emergency procedure?",
            initial_result=initial_result,
            top_k=8,
            min_score=0.8,
        )
    ]

    assert events[:2] == [
        AgentLoopProgress(iteration=1, stage="started", tool="search"),
        AgentLoopProgress(iteration=1, stage="completed", tool="search"),
    ]
    completed = events[-1]
    assert isinstance(completed, AgentGatherCompleted)
    assert completed.finish_reason == "evidence_sufficient"
    assert completed.result.no_answer is False
    assert {row.text for row in completed.result.evidence} == {
        "Initial weak evidence",
        "Expanded strong evidence",
    }


async def test_multi_part_gather_requires_two_useful_tool_calls_before_early_stop() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    first = _evidence(score=0.65, text="First clause")
    second = _evidence(score=0.93, text="Second clause")
    third = _evidence(score=0.94, text="Third clause")
    searches = 0

    class Backend:
        async def search(self, query: str, metadata: object) -> tuple[AuthorizedToolEvidence, ...]:
            nonlocal searches
            searches += 1
            row = second if searches == 1 else third
            return (AuthorizedToolEvidence(org_id, workspace_id, row),)

        async def get_document(self, document_id: object) -> tuple[AuthorizedToolEvidence, ...]:
            raise AssertionError(document_id)

    async def planner(state: AgentLoopState) -> AgentAction:
        return AgentAction.tool(
            AgentToolCall(name="search", query=f"clause {state.iteration + 1}")
        )

    gatherer = AgentGatherer(
        planner,
        RetrievalToolExecutor(Backend(), org_id=org_id, workspace_id=workspace_id),
    )
    events = [
        event
        async for event in gatherer.stream(
            query="Compare both clauses and explain differences",
            initial_result=RetrievalResult(chunks=[], no_answer=True, evidence=(first,)),
            top_k=8,
            min_score=0.8,
            minimum_tool_calls=2,
        )
    ]

    completed = events[-1]
    assert isinstance(completed, AgentGatherCompleted)
    assert completed.finish_reason == "evidence_sufficient"
    assert searches == 2


async def test_gather_never_upgrades_weak_evidence_without_a_passing_score() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    weak = _evidence(score=0.4, text="Weak")

    class Backend:
        async def search(self, query: str, metadata: object) -> tuple[AuthorizedToolEvidence, ...]:
            raise AssertionError((query, metadata))

        async def get_document(self, document_id: object) -> tuple[AuthorizedToolEvidence, ...]:
            raise AssertionError(document_id)

    async def planner(_state: AgentLoopState) -> AgentAction:
        return AgentAction.finish()

    gatherer = AgentGatherer(
        planner,
        RetrievalToolExecutor(Backend(), org_id=org_id, workspace_id=workspace_id),
    )
    initial_result = RetrievalResult(
        chunks=[],
        no_answer=True,
        evidence=(replace(weak, dense_score=0.4),),
    )

    events = [
        event
        async for event in gatherer.stream(
            query="What is the policy?",
            initial_result=initial_result,
            top_k=8,
            min_score=0.8,
        )
    ]

    completed = events[-1]
    assert isinstance(completed, AgentGatherCompleted)
    assert completed.result.no_answer is True


def test_runtime_factory_is_gated_by_measured_structured_output_support() -> None:
    runtime = ModelRuntime(
        litellm_model="openai/gpt-test",
        api_key=None,
        api_base=None,
        max_output_tokens=512,
    )
    session_factory = cast(async_sessionmaker[AsyncSession], object())
    context = cast(TenantContext, SimpleNamespace(org_id=uuid4()))
    workspace_id = uuid4()

    assert (
        build_agent_gatherer_factory(
            runtime,
            session_factory=session_factory,
            context=context,
            workspace_id=workspace_id,
            structured_output_measured=False,
        )
        is None
    )
    factory = build_agent_gatherer_factory(
        runtime,
        session_factory=session_factory,
        context=context,
        workspace_id=workspace_id,
        structured_output_measured=True,
    )

    assert factory is not None
    assert isinstance(factory("Find the approved procedure"), AgentGatherer)
