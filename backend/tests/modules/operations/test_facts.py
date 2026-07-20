from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Message
from openrag.modules.operations.facts import (
    RunFactSource,
    RunObservation,
    RunStageTimer,
    build_run_fact_insert,
    build_unprojected_run_query,
    project_run_fact,
    record_run_fact,
)
from openrag.modules.operations.models import RagRunFact
from openrag.modules.runs.models import AgentRun, RunContextLedger
from openrag.modules.runs.schemas import RunCreate
from openrag.modules.runs.service import accept_run
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace, WorkspaceMember


def _source(**changes: object) -> RunFactSource:
    accepted_at = datetime(2026, 7, 20, 10, 0, 0)
    values: dict[str, object] = {
        "org_id": uuid4(),
        "workspace_id": uuid4(),
        "run_id": uuid4(),
        "model_id": uuid4(),
        "trace_id": "a" * 32,
        "status": "completed",
        "route": "rag",
        "error_code": None,
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "attempts": 1,
        "accepted_at": accepted_at,
        "first_token_at": accepted_at + timedelta(milliseconds=800),
        "finished_at": accepted_at + timedelta(milliseconds=1500),
        "answer_status": "grounded",
        "retrieval_count": 6,
        "citation_count": 3,
        "memory_item_count": 2,
    }
    values.update(changes)
    return RunFactSource(**values)  # type: ignore[arg-type]


def test_projection_derives_bounded_grounded_metrics() -> None:
    fact = project_run_fact(
        _source(),
        RunObservation(
            route_ms=2,
            retrieval_ms=110,
            provider_ms=650,
            persistence_ms=20,
        ),
        environment="prod",
        release="2026.07.20",
    )

    assert fact.outcome == "grounded"
    assert fact.latency_ms == 1500
    assert fact.ttft_ms == 800
    assert fact.retrieval_count == 6
    assert fact.citation_count == 3
    assert fact.memory_item_count == 2
    assert (
        fact.model_dump()
        .keys()
        .isdisjoint({"prompt", "response", "query", "document_text", "memory"})
    )


@pytest.mark.parametrize(
    ("source", "outcome"),
    [
        ({"status": "failed", "error_code": "provider_transient"}, "failed"),
        ({"status": "cancelled"}, "cancelled"),
        ({"answer_status": "refused"}, "no_answer"),
        ({"answer_status": None, "citation_count": 0}, "no_answer"),
        ({"route": "direct", "answer_status": None}, "conversational"),
    ],
)
def test_projection_maps_every_terminal_outcome(
    source: dict[str, object],
    outcome: str,
) -> None:
    fact = project_run_fact(
        _source(**source),
        RunObservation(),
        environment="test",
        release=None,
    )

    assert fact.outcome == outcome


def test_observation_rejects_negative_or_impossible_stage_timings() -> None:
    with pytest.raises(ValueError, match="run_observation_invalid"):
        RunObservation(retrieval_ms=-1)


def test_stage_timer_uses_monotonic_boundaries() -> None:
    ticks = iter((0, 2_000_000, 5_000_000, 25_000_000, 30_000_000, 32_000_000, 35_000_000))
    timer = RunStageTimer(clock_ns=lambda: next(ticks))

    timer.route_selected()
    timer.retrieval_started()
    timer.retrieval_completed()
    timer.first_token()
    timer.persistence_started()
    timer.persistence_completed()

    assert timer.snapshot() == RunObservation(
        route_ms=2,
        retrieval_ms=20,
        provider_ms=5,
        persistence_ms=3,
    )


def test_stage_timer_records_first_token_only_once_when_under_one_millisecond() -> None:
    ticks = iter((0, 1_000_000, 1_500_000, 101_000_000))
    timer = RunStageTimer(clock_ns=lambda: next(ticks))

    timer.route_selected()
    timer.first_token()
    timer.first_token()

    assert timer.snapshot().provider_ms == 0


def test_fact_insert_is_idempotent_per_organization_and_run() -> None:
    fact = project_run_fact(
        _source(),
        RunObservation(),
        environment="test",
        release=None,
    )

    sql = str(
        build_run_fact_insert(fact).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "ON CONFLICT (org_id, run_id) DO NOTHING" in sql


def test_reconciliation_query_is_bounded_and_selects_only_missing_terminal_runs() -> None:
    sql = str(
        build_unprojected_run_query().compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "agent_runs.status IN ('completed', 'failed', 'cancelled')" in sql
    assert "NOT (EXISTS" in sql
    assert "LIMIT 1" in sql


def test_projection_requires_a_finished_terminal_run() -> None:
    with pytest.raises(ValueError, match="run_fact_not_terminal"):
        project_run_fact(
            _source(status="running", finished_at=None),
            RunObservation(),
            environment="test",
            release=None,
        )


async def test_record_run_fact_is_idempotent_and_uses_durable_counts(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name="Operations facts")
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            org_id=seeded_user.org_id,
            workspace_id=workspace.id,
            user_id=seeded_user.id,
        )
    )
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.commit()
    context = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        authorization=AuthorizationSnapshot(
            user_id=seeded_user.id,
            org_id=seeded_user.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"chat.use"}),
            workspace_permissions={},
            workspace_ids=frozenset({workspace.id}),
        ),
    )
    accepted = await accept_run(
        session,
        context,
        chat.id,
        RunCreate(content="private question", client_request_id=uuid4()),
    )
    assistant = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        parent_message_id=accepted.run.input_message_id,
        sibling_index=0,
        role="assistant",
        content="private answer",
        answer_status="refused",
        refusal_reason="below_threshold",
    )
    session.add(assistant)
    await session.flush()
    run = await session.get(AgentRun, accepted.run.id)
    assert run is not None
    run.status = "completed"
    run.route = "rag"
    run.assistant_message_id = assistant.id
    run.prompt_tokens = 10
    run.completion_tokens = 4
    run.first_token_at = run.accepted_at + timedelta(milliseconds=20)
    run.finished_at = run.accepted_at + timedelta(milliseconds=50)
    session.add(
        RunContextLedger(
            org_id=seeded_user.org_id,
            workspace_id=workspace.id,
            run_id=run.id,
            attempt=1,
            route="rag",
            budget_tokens=1000,
            estimated_prompt_tokens=10,
            memory_tokens=4,
            memory_items=2,
            history_tokens=0,
            history_messages=0,
            retrieval_tokens=20,
            retrieval_items=5,
            selection_digest="d" * 64,
        )
    )
    await session.commit()

    factory = build_session_factory(engine)
    await record_run_fact(
        factory,
        run.id,
        RunObservation(retrieval_ms=12),
        environment="test",
        release=None,
    )
    await record_run_fact(
        factory,
        run.id,
        RunObservation(retrieval_ms=12),
        environment="test",
        release=None,
    )

    assert await session.scalar(select(func.count()).select_from(RagRunFact)) == 1
    fact = await session.scalar(select(RagRunFact))
    assert fact is not None
    assert fact.outcome == "no_answer"
    assert fact.retrieval_count == 5
    assert fact.memory_item_count == 2
    assert fact.citation_count == 0
    assert not hasattr(fact, "query")
