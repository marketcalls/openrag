"""Crash-recoverable, one-case-per-lease evaluation worker."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.telemetry import record_active_evaluation
from openrag.modules.auth.models import User
from openrag.modules.chat.llm import LLMStreamer
from openrag.modules.evaluations.executor import ProductionEvaluationExecutor
from openrag.modules.evaluations.metrics import aggregate_case_metrics
from openrag.modules.evaluations.models import (
    EvaluationCase,
    EvaluationCaseEvidence,
    EvaluationCaseResult,
    EvaluationRun,
)
from openrag.modules.evaluations.runtime import (
    EvaluationCaseScore,
    EvaluationLeaseClaim,
    claim_next_evaluation_run,
    renew_evaluation_lease,
    score_observation,
)
from openrag.modules.models.models import Model
from openrag.modules.orchestration.runtime import create_model_streamer
from openrag.modules.tenancy.authorization import resolve_authorization
from openrag.modules.tenancy.context import TenantContext

EvaluationTickResult = Literal[
    "idle",
    "contested",
    "case_completed",
    "completed",
    "failed",
]


@dataclass(frozen=True, slots=True)
class PreparedEvaluationCase:
    case_id: UUID
    sequence: int
    question: str
    should_refuse: bool
    expected_evidence_ids: frozenset[UUID]
    context: TenantContext
    workspace_id: UUID
    model_name: str
    streamer: LLMStreamer
    judge_model_name: str | None
    judge_streamer: LLMStreamer | None


def _clear_lease(run: EvaluationRun) -> None:
    run.lease_owner = None
    run.lease_token = None
    run.lease_expires_at = None


async def _prepare_case(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    claim: EvaluationLeaseClaim,
) -> PreparedEvaluationCase | None:
    async with session_factory() as session:
        run = await session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.id == claim.run_id,
                EvaluationRun.status == "running",
                EvaluationRun.lease_token == claim.token,
                EvaluationRun.lease_owner == claim.owner,
            )
        )
        if run is None:
            return None
        case = await session.scalar(
            select(EvaluationCase)
            .where(
                EvaluationCase.dataset_version_id == run.dataset_version_id,
                EvaluationCase.sequence <= run.total_cases,
                ~exists().where(
                    EvaluationCaseResult.run_id == run.id,
                    EvaluationCaseResult.case_id == EvaluationCase.id,
                ),
            )
            .order_by(EvaluationCase.sequence)
            .limit(1)
        )
        if case is None:
            return None
        user = await session.get(User, run.created_by)
        if user is None or not user.active or user.org_id != run.org_id:
            raise RuntimeError("evaluation_principal_revoked")
        context = TenantContext(
            user_id=user.id,
            org_id=user.org_id,
            authorization=await resolve_authorization(session, user),
        )
        model = await session.get(Model, run.model_id)
        if model is None or not model.enabled or not model.supports_chat_completion:
            raise RuntimeError("evaluation_model_unavailable")
        streamer = await create_model_streamer(session, model, settings)
        judge_model: Model | None = None
        judge_streamer: LLMStreamer | None = None
        if run.use_llm_judge:
            if run.evaluator_model_id is None:
                raise RuntimeError("evaluation_judge_model_missing")
            judge_model = await session.get(Model, run.evaluator_model_id)
            if (
                judge_model is None
                or not judge_model.enabled
                or not judge_model.supports_structured_json
                or not judge_model.supports_verifier
            ):
                raise RuntimeError("evaluation_judge_model_unavailable")
            judge_streamer = await create_model_streamer(session, judge_model, settings)
        expected = frozenset(
            (
                await session.scalars(
                    select(EvaluationCaseEvidence.evidence_span_id).where(
                        EvaluationCaseEvidence.case_id == case.id
                    )
                )
            ).all()
        )
        return PreparedEvaluationCase(
            case_id=case.id,
            sequence=case.sequence,
            question=case.question,
            should_refuse=case.should_refuse,
            expected_evidence_ids=expected,
            context=context,
            workspace_id=run.workspace_id,
            model_name=model.litellm_model_name,
            streamer=streamer,
            judge_model_name=(
                judge_model.litellm_model_name if judge_model is not None else None
            ),
            judge_streamer=judge_streamer,
        )


async def _execute_case(
    session_factory: async_sessionmaker[AsyncSession],
    prepared: PreparedEvaluationCase,
    settings: Settings,
) -> EvaluationCaseScore:
    async with session_factory() as session:
        observation = await ProductionEvaluationExecutor().evaluate(
            session=session,
            context=prepared.context,
            workspace_id=prepared.workspace_id,
            question=prepared.question,
            model_name=prepared.model_name,
            streamer=prepared.streamer,
            judge_model_name=prepared.judge_model_name,
            judge_streamer=prepared.judge_streamer,
            context_budget=settings.chat_context_token_budget,
        )
    return score_observation(
        expected_evidence_ids=set(prepared.expected_evidence_ids),
        should_refuse=prepared.should_refuse,
        observation=observation,
        k=8,
    )


def _result_metrics(result: EvaluationCaseResult) -> dict[str, float | None]:
    return {
        "recall": result.recall,
        "precision": result.precision,
        "mrr": result.mrr,
        "ndcg": result.ndcg,
        "citation_precision": result.citation_precision,
        "citation_recall": result.citation_recall,
        "groundedness": result.groundedness,
        "answer_relevance": result.answer_relevance,
        "correct_refusal": result.correct_refusal,
    }


async def _finish_run(
    session: AsyncSession,
    run: EvaluationRun,
) -> None:
    completed = list(
        (
            await session.scalars(
                select(EvaluationCaseResult).where(
                    EvaluationCaseResult.run_id == run.id,
                    EvaluationCaseResult.status == "completed",
                )
            )
        ).all()
    )
    aggregate = aggregate_case_metrics([_result_metrics(result) for result in completed])
    run.recall = aggregate.recall
    run.precision = aggregate.precision
    run.mrr = aggregate.mrr
    run.ndcg = aggregate.ndcg
    run.citation_precision = aggregate.citation_precision
    run.citation_recall = aggregate.citation_recall
    run.groundedness = aggregate.groundedness
    run.answer_relevance = aggregate.answer_relevance
    run.correct_refusal = aggregate.correct_refusal
    run.status = "completed"
    run.error_code = "evaluation_case_failures" if run.failed_cases else None
    run.finished_at = naive_utc()
    _clear_lease(run)
    record_active_evaluation(
        groundedness=run.groundedness,
        answer_relevance=run.answer_relevance,
        correct_refusal=run.correct_refusal,
    )


async def _persist_score(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EvaluationLeaseClaim,
    prepared: PreparedEvaluationCase,
    score: EvaluationCaseScore,
) -> EvaluationTickResult:
    async with session_factory.begin() as session:
        run = await session.scalar(
            select(EvaluationRun)
            .where(
                EvaluationRun.id == claim.run_id,
                EvaluationRun.status == "running",
                EvaluationRun.lease_token == claim.token,
                EvaluationRun.lease_owner == claim.owner,
            )
            .with_for_update()
        )
        if run is None:
            return "contested"
        existing = await session.scalar(
            select(EvaluationCaseResult.id).where(
                EvaluationCaseResult.run_id == run.id,
                EvaluationCaseResult.case_id == prepared.case_id,
            )
        )
        if existing is not None:
            run.status = "queued"
            _clear_lease(run)
            return "contested"
        session.add(
            EvaluationCaseResult(
                org_id=run.org_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                case_id=prepared.case_id,
                sequence=prepared.sequence,
                status="completed",
                did_refuse=score.did_refuse,
                retrieved_evidence_ids=list(score.retrieved_evidence_ids),
                cited_evidence_ids=list(score.cited_evidence_ids),
                recall=score.recall,
                precision=score.precision,
                mrr=score.mrr,
                ndcg=score.ndcg,
                citation_precision=score.citation_precision,
                citation_recall=score.citation_recall,
                groundedness=score.groundedness,
                answer_relevance=score.answer_relevance,
                correct_refusal=score.correct_refusal,
                latency_ms=score.latency_ms,
                prompt_tokens=score.prompt_tokens,
                completion_tokens=score.completion_tokens,
                estimated_cost_microusd=score.estimated_cost_microusd,
                answer_digest=score.answer_digest,
            )
        )
        run.completed_cases += 1
        run.consumed_tokens += score.prompt_tokens + score.completion_tokens
        run.consumed_cost_microusd += score.estimated_cost_microusd
        await session.flush()
        if (
            run.consumed_tokens > run.max_tokens
            or run.consumed_cost_microusd > run.max_cost_microusd
        ):
            run.status = "failed"
            run.error_code = "evaluation_budget_exceeded"
            run.finished_at = naive_utc()
            _clear_lease(run)
            return "failed"
        if run.completed_cases + run.failed_cases >= run.total_cases:
            await _finish_run(session, run)
            return "completed"
        run.status = "queued"
        _clear_lease(run)
        return "case_completed"


async def _persist_case_failure(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EvaluationLeaseClaim,
    prepared: PreparedEvaluationCase,
    *,
    error_code: str,
) -> EvaluationTickResult:
    async with session_factory.begin() as session:
        run = await session.scalar(
            select(EvaluationRun)
            .where(
                EvaluationRun.id == claim.run_id,
                EvaluationRun.status == "running",
                EvaluationRun.lease_token == claim.token,
                EvaluationRun.lease_owner == claim.owner,
            )
            .with_for_update()
        )
        if run is None:
            return "contested"
        existing = await session.scalar(
            select(EvaluationCaseResult.id).where(
                EvaluationCaseResult.run_id == run.id,
                EvaluationCaseResult.case_id == prepared.case_id,
            )
        )
        if existing is None:
            session.add(
                EvaluationCaseResult(
                    org_id=run.org_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    case_id=prepared.case_id,
                    sequence=prepared.sequence,
                    status="failed",
                    retrieved_evidence_ids=[],
                    cited_evidence_ids=[],
                    latency_ms=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    estimated_cost_microusd=0,
                    error_code=error_code,
                )
            )
            run.failed_cases += 1
            await session.flush()
        if run.completed_cases + run.failed_cases >= run.total_cases:
            await _finish_run(session, run)
            return "completed"
        run.status = "queued"
        _clear_lease(run)
        return "failed"


async def _fail_run(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EvaluationLeaseClaim,
    *,
    error_code: str,
) -> EvaluationTickResult:
    async with session_factory.begin() as session:
        run = await session.scalar(
            select(EvaluationRun)
            .where(
                EvaluationRun.id == claim.run_id,
                EvaluationRun.status == "running",
                EvaluationRun.lease_token == claim.token,
                EvaluationRun.lease_owner == claim.owner,
            )
            .with_for_update()
        )
        if run is None:
            return "contested"
        run.status = "failed"
        run.error_code = error_code
        run.finished_at = naive_utc()
        _clear_lease(run)
        return "failed"


async def execute_evaluation_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    owner: str,
) -> EvaluationTickResult:
    claim = await claim_next_evaluation_run(
        session_factory,
        owner=owner,
        lease_seconds=settings.evaluation_lease_seconds,
    )
    if claim is None:
        return "idle"
    try:
        prepared = await _prepare_case(session_factory, settings, claim)
    except Exception:  # noqa: BLE001 - only a safe terminal code is persisted
        return await _fail_run(
            session_factory,
            claim,
            error_code="evaluation_preparation_failed",
        )
    if prepared is None:
        return await _fail_run(
            session_factory,
            claim,
            error_code="evaluation_case_missing",
        )

    execution = asyncio.create_task(_execute_case(session_factory, prepared, settings))
    heartbeat_seconds = max(10.0, settings.evaluation_lease_seconds / 3)
    while True:
        done, _pending = await asyncio.wait({execution}, timeout=heartbeat_seconds)
        if done:
            break
        renewed = await renew_evaluation_lease(
            session_factory,
            claim,
            lease_seconds=settings.evaluation_lease_seconds,
        )
        if not renewed:
            execution.cancel()
            with suppress(asyncio.CancelledError):
                await execution
            return "contested"
    try:
        score = await execution
    except Exception:  # noqa: BLE001 - provider detail must not enter persistence
        return await _persist_case_failure(
            session_factory,
            claim,
            prepared,
            error_code="evaluation_case_execution_failed",
        )
    return await _persist_score(session_factory, claim, prepared, score)
