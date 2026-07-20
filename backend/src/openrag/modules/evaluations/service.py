"""Transactional service for sealed evaluation corpora and budgeted runs."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, InvalidRequestError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.models import DocumentEvidenceSpan, DocumentVersion
from openrag.modules.evaluations.automation import (
    build_due_policy_query,
    config_trigger_key,
    next_scheduled_at,
    scheduled_trigger_key,
)
from openrag.modules.evaluations.models import (
    EvaluationCase,
    EvaluationCaseEvidence,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationPolicy,
    EvaluationRun,
)
from openrag.modules.evaluations.schemas import (
    EvaluationCaseOut,
    EvaluationCaseResultOut,
    EvaluationDatasetCreate,
    EvaluationDatasetVersionCreate,
    EvaluationDatasetVersionDetail,
    EvaluationDatasetVersionOut,
    EvaluationEvidenceCreate,
    EvaluationPolicyUpsert,
    EvaluationRunCreate,
    EvaluationRunDetail,
    EvaluationRunOut,
)
from openrag.modules.models.models import Model
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace


async def _validate_run_models(
    session: AsyncSession,
    *,
    model_id: UUID,
    evaluator_model_id: UUID | None,
) -> Model:
    model = await session.scalar(
        select(Model).where(
            Model.id == model_id,
            Model.enabled.is_(True),
            Model.supports_chat_completion.is_(True),
        )
    )
    if model is None:
        raise InvalidRequestError("evaluation model is not an enabled chat model")
    if evaluator_model_id is not None:
        evaluator = await session.scalar(
            select(Model).where(
                Model.id == evaluator_model_id,
                Model.enabled.is_(True),
                Model.supports_chat_completion.is_(True),
                Model.supports_structured_json.is_(True),
                Model.supports_verifier.is_(True),
            )
        )
        if evaluator is None:
            raise InvalidRequestError("evaluator model lacks verifier capabilities")
    return model


async def _latest_dataset_version(
    session: AsyncSession,
    *,
    org_id: UUID,
    dataset_id: UUID,
) -> EvaluationDatasetVersion | None:
    return cast(
        EvaluationDatasetVersion | None,
        await session.scalar(
            select(EvaluationDatasetVersion)
            .where(
                EvaluationDatasetVersion.org_id == org_id,
                EvaluationDatasetVersion.dataset_id == dataset_id,
            )
            .order_by(EvaluationDatasetVersion.version.desc())
            .limit(1)
        ),
    )


def _corpus_digest(body: EvaluationDatasetVersionCreate) -> str:
    canonical = [
        {
            "question": case.question.strip(),
            "should_refuse": case.should_refuse,
            "expected_evidence": sorted(
                (
                    str(item.document_version_id),
                    str(item.evidence_span_id),
                )
                for item in case.expected_evidence
            ),
        }
        for case in body.cases
    ]
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def create_dataset(
    session: AsyncSession,
    context: TenantContext,
    body: EvaluationDatasetCreate,
) -> EvaluationDataset:
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.org_id == context.org_id,
            Workspace.id == body.workspace_id,
        )
    )
    if workspace is None:
        raise NotFoundError("workspace not found")
    dataset = EvaluationDataset(
        org_id=context.org_id,
        workspace_id=workspace.id,
        name=body.name.strip(),
        description=body.description.strip(),
        created_by=context.user_id,
    )
    session.add(dataset)
    try:
        await session.flush()
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action="evaluation_dataset.created",
            target_type="evaluation_dataset",
            target_id=str(dataset.id),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("evaluation dataset name already exists") from exc
    return dataset


async def list_datasets(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID | None,
) -> list[EvaluationDataset]:
    statement = select(EvaluationDataset).where(EvaluationDataset.org_id == context.org_id)
    if workspace_id is not None:
        statement = statement.where(EvaluationDataset.workspace_id == workspace_id)
    return list(
        (
            await session.scalars(
                statement.order_by(EvaluationDataset.created_at.desc()).limit(100)
            )
        ).all()
    )


async def _dataset(
    session: AsyncSession,
    context: TenantContext,
    dataset_id: UUID,
    *,
    lock: bool = False,
) -> EvaluationDataset:
    statement = select(EvaluationDataset).where(
        EvaluationDataset.org_id == context.org_id,
        EvaluationDataset.id == dataset_id,
    )
    if lock:
        statement = statement.with_for_update()
    dataset = await session.scalar(statement)
    if dataset is None:
        raise NotFoundError("evaluation dataset not found")
    return dataset


async def _validate_evidence(
    session: AsyncSession,
    *,
    org_id: UUID,
    workspace_id: UUID,
    evidence: list[EvaluationEvidenceCreate],
) -> None:
    expected = {
        (item.document_version_id, item.evidence_span_id)
        for item in evidence
    }
    if not expected:
        return
    rows = await session.execute(
        select(DocumentEvidenceSpan.document_version_id, DocumentEvidenceSpan.id)
        .join(DocumentVersion, DocumentVersion.id == DocumentEvidenceSpan.document_version_id)
        .where(
            DocumentEvidenceSpan.org_id == org_id,
            DocumentVersion.org_id == org_id,
            DocumentVersion.workspace_id == workspace_id,
            DocumentVersion.state == "approved",
            tuple_(
                DocumentEvidenceSpan.document_version_id,
                DocumentEvidenceSpan.id,
            ).in_(expected),
        )
    )
    if set(rows.tuples()) != expected:
        raise InvalidRequestError("evaluation evidence must reference approved workspace content")


async def create_dataset_version(
    session: AsyncSession,
    context: TenantContext,
    dataset_id: UUID,
    body: EvaluationDatasetVersionCreate,
) -> EvaluationDatasetVersion:
    dataset = await _dataset(session, context, dataset_id, lock=True)
    all_evidence = [item for case in body.cases for item in case.expected_evidence]
    await _validate_evidence(
        session,
        org_id=dataset.org_id,
        workspace_id=dataset.workspace_id,
        evidence=all_evidence,
    )
    latest = await session.scalar(
        select(func.max(EvaluationDatasetVersion.version)).where(
            EvaluationDatasetVersion.dataset_id == dataset.id
        )
    )
    version = EvaluationDatasetVersion(
        id=uuid4(),
        org_id=dataset.org_id,
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        version=(latest or 0) + 1,
        label=body.label.strip() if body.label else None,
        status="sealed",
        case_count=len(body.cases),
        content_digest=_corpus_digest(body),
        created_by=context.user_id,
    )
    session.add(version)
    for sequence, case_body in enumerate(body.cases, start=1):
        case = EvaluationCase(
            id=uuid4(),
            org_id=dataset.org_id,
            workspace_id=dataset.workspace_id,
            dataset_version_id=version.id,
            sequence=sequence,
            question=case_body.question.strip(),
            should_refuse=case_body.should_refuse,
        )
        session.add(case)
        for position, evidence_body in enumerate(case_body.expected_evidence, start=1):
            session.add(
                EvaluationCaseEvidence(
                    org_id=dataset.org_id,
                    workspace_id=dataset.workspace_id,
                    case_id=case.id,
                    document_version_id=evidence_body.document_version_id,
                    evidence_span_id=evidence_body.evidence_span_id,
                    position=position,
                )
            )
    try:
        await session.flush()
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action="evaluation_dataset_version.sealed",
            target_type="evaluation_dataset_version",
            target_id=str(version.id),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("evaluation dataset version conflicted with another writer") from exc
    return version


async def list_dataset_versions(
    session: AsyncSession,
    context: TenantContext,
    dataset_id: UUID,
) -> list[EvaluationDatasetVersion]:
    await _dataset(session, context, dataset_id)
    return list(
        (
            await session.scalars(
                select(EvaluationDatasetVersion)
                .where(
                    EvaluationDatasetVersion.org_id == context.org_id,
                    EvaluationDatasetVersion.dataset_id == dataset_id,
                )
                .order_by(EvaluationDatasetVersion.version.desc())
                .limit(100)
            )
        ).all()
    )


async def get_dataset_version_detail(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
) -> EvaluationDatasetVersionDetail:
    version = await session.scalar(
        select(EvaluationDatasetVersion).where(
            EvaluationDatasetVersion.org_id == context.org_id,
            EvaluationDatasetVersion.id == version_id,
        )
    )
    if version is None:
        raise NotFoundError("evaluation dataset version not found")
    cases = list(
        (
            await session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.dataset_version_id == version.id)
                .order_by(EvaluationCase.sequence)
            )
        ).all()
    )
    evidence_rows = list(
        (
            await session.scalars(
                select(EvaluationCaseEvidence)
                .where(EvaluationCaseEvidence.case_id.in_([case.id for case in cases]))
                .order_by(EvaluationCaseEvidence.position)
            )
        ).all()
    )
    by_case: dict[UUID, list[EvaluationEvidenceCreate]] = defaultdict(list)
    for item in evidence_rows:
        by_case[item.case_id].append(
            EvaluationEvidenceCreate(
                document_version_id=item.document_version_id,
                evidence_span_id=item.evidence_span_id,
            )
        )
    return EvaluationDatasetVersionDetail(
        **EvaluationDatasetVersionOut.model_validate(version).model_dump(),
        cases=[
            EvaluationCaseOut(
                id=case.id,
                sequence=case.sequence,
                question=case.question,
                should_refuse=case.should_refuse,
                expected_evidence=by_case[case.id],
            )
            for case in cases
        ],
    )


async def create_run(
    session: AsyncSession,
    context: TenantContext,
    body: EvaluationRunCreate,
) -> EvaluationRun:
    version = await session.scalar(
        select(EvaluationDatasetVersion).where(
            EvaluationDatasetVersion.org_id == context.org_id,
            EvaluationDatasetVersion.id == body.dataset_version_id,
        )
    )
    if version is None:
        raise NotFoundError("evaluation dataset version not found")
    model = await _validate_run_models(
        session,
        model_id=body.model_id,
        evaluator_model_id=body.evaluator_model_id,
    )
    request_id = body.client_request_id or uuid4()
    existing = await session.scalar(
        select(EvaluationRun).where(
            EvaluationRun.org_id == context.org_id,
            EvaluationRun.created_by == context.user_id,
            EvaluationRun.client_request_id == request_id,
        )
    )
    if existing is not None:
        return existing
    run = EvaluationRun(
        org_id=version.org_id,
        workspace_id=version.workspace_id,
        dataset_version_id=version.id,
        model_id=model.id,
        evaluator_model_id=body.evaluator_model_id,
        use_llm_judge=body.use_llm_judge,
        client_request_id=request_id,
        status="queued",
        max_cases=body.max_cases,
        max_tokens=body.max_tokens,
        max_cost_microusd=body.max_cost_microusd,
        total_cases=min(version.case_count, body.max_cases),
        created_by=context.user_id,
    )
    session.add(run)
    try:
        await session.flush()
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action="evaluation_run.queued",
            target_type="evaluation_run",
            target_id=str(run.id),
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raced = await session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.org_id == context.org_id,
                EvaluationRun.created_by == context.user_id,
                EvaluationRun.client_request_id == request_id,
            )
        )
        if raced is None:
            raise
        return raced
    return run


async def upsert_policy(
    session: AsyncSession,
    context: TenantContext,
    body: EvaluationPolicyUpsert,
) -> EvaluationPolicy:
    dataset = await session.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.org_id == context.org_id,
            EvaluationDataset.id == body.dataset_id,
            EvaluationDataset.archived.is_(False),
        )
    )
    if dataset is None:
        raise NotFoundError("evaluation dataset not found")
    if await _latest_dataset_version(
        session,
        org_id=context.org_id,
        dataset_id=dataset.id,
    ) is None:
        raise InvalidRequestError("evaluation policy requires a sealed dataset version")
    await _validate_run_models(
        session,
        model_id=body.model_id,
        evaluator_model_id=body.evaluator_model_id,
    )
    policy = await session.scalar(
        select(EvaluationPolicy)
        .where(
            EvaluationPolicy.org_id == context.org_id,
            EvaluationPolicy.workspace_id == dataset.workspace_id,
            EvaluationPolicy.dataset_id == dataset.id,
        )
        .with_for_update()
    )
    now = naive_utc()
    action = "evaluation_policy.updated"
    if policy is None:
        action = "evaluation_policy.created"
        policy = EvaluationPolicy(
            org_id=context.org_id,
            workspace_id=dataset.workspace_id,
            dataset_id=dataset.id,
            model_id=body.model_id,
            evaluator_model_id=body.evaluator_model_id,
            use_llm_judge=body.use_llm_judge,
            enabled=body.enabled,
            trigger_on_config_change=body.trigger_on_config_change,
            interval_hours=body.interval_hours,
            max_cases=body.max_cases,
            max_tokens=body.max_tokens,
            max_cost_microusd=body.max_cost_microusd,
            next_run_at=next_scheduled_at(
                now,
                interval_hours=body.interval_hours,
            ),
            created_by=context.user_id,
        )
        session.add(policy)
    else:
        policy.model_id = body.model_id
        policy.evaluator_model_id = body.evaluator_model_id
        policy.use_llm_judge = body.use_llm_judge
        policy.enabled = body.enabled
        policy.trigger_on_config_change = body.trigger_on_config_change
        policy.interval_hours = body.interval_hours
        policy.max_cases = body.max_cases
        policy.max_tokens = body.max_tokens
        policy.max_cost_microusd = body.max_cost_microusd
        policy.next_run_at = next_scheduled_at(
            now,
            interval_hours=body.interval_hours,
        )
        policy.last_error_code = None
    try:
        await session.flush()
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action=action,
            target_type="evaluation_policy",
            target_id=str(policy.id),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("evaluation policy conflicted with another writer") from exc
    return policy


async def list_policies(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID | None,
) -> list[EvaluationPolicy]:
    statement = select(EvaluationPolicy).where(
        EvaluationPolicy.org_id == context.org_id
    )
    if workspace_id is not None:
        statement = statement.where(EvaluationPolicy.workspace_id == workspace_id)
    return list(
        (
            await session.scalars(
                statement.order_by(EvaluationPolicy.created_at.desc()).limit(100)
            )
        ).all()
    )


async def _queue_policy_run(
    session: AsyncSession,
    policy: EvaluationPolicy,
    *,
    trigger_kind: str,
    trigger_key: str,
    now: datetime,
) -> EvaluationRun | None:
    existing = await session.scalar(
        select(EvaluationRun.id).where(
            EvaluationRun.policy_id == policy.id,
            EvaluationRun.trigger_key == trigger_key,
        )
    )
    if existing is not None:
        return None
    version = await _latest_dataset_version(
        session,
        org_id=policy.org_id,
        dataset_id=policy.dataset_id,
    )
    if version is None:
        policy.last_error_code = "evaluation_policy_dataset_unversioned"
        return None
    try:
        await _validate_run_models(
            session,
            model_id=policy.model_id,
            evaluator_model_id=policy.evaluator_model_id,
        )
    except InvalidRequestError:
        policy.last_error_code = "evaluation_policy_model_invalid"
        return None
    run = EvaluationRun(
        org_id=policy.org_id,
        workspace_id=policy.workspace_id,
        dataset_version_id=version.id,
        model_id=policy.model_id,
        evaluator_model_id=policy.evaluator_model_id,
        use_llm_judge=policy.use_llm_judge,
        policy_id=policy.id,
        trigger_kind=trigger_kind,
        trigger_key=trigger_key,
        client_request_id=None,
        status="queued",
        max_cases=policy.max_cases,
        max_tokens=policy.max_tokens,
        max_cost_microusd=policy.max_cost_microusd,
        total_cases=min(version.case_count, policy.max_cases),
        created_by=policy.created_by,
    )
    session.add(run)
    policy.last_enqueued_at = now
    policy.last_error_code = None
    await session.flush()
    await record_audit(
        session,
        org_id=policy.org_id,
        actor_id=None,
        action="evaluation_run.automated_queued",
        target_type="evaluation_run",
        target_id=str(run.id),
    )
    return run


async def schedule_due_policies(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> int:
    scheduled_at = now or naive_utc()
    policies = list(
        (
            await session.scalars(
                build_due_policy_query(scheduled_at, limit=limit)
            )
        ).all()
    )
    queued = 0
    for policy in policies:
        trigger_key = scheduled_trigger_key(policy.next_run_at)
        policy.next_run_at = next_scheduled_at(
            scheduled_at,
            interval_hours=policy.interval_hours,
        )
        run = await _queue_policy_run(
            session,
            policy,
            trigger_kind="scheduled",
            trigger_key=trigger_key,
            now=scheduled_at,
        )
        queued += int(run is not None)
    await session.commit()
    return queued


async def queue_config_change_runs(
    session: AsyncSession,
    *,
    org_id: UUID,
    workspace_id: UUID,
    configuration_fingerprint: str,
    now: datetime | None = None,
) -> int:
    queued_at = now or naive_utc()
    trigger_key = config_trigger_key(configuration_fingerprint)
    policies = list(
        (
            await session.scalars(
                select(EvaluationPolicy)
                .where(
                    EvaluationPolicy.org_id == org_id,
                    EvaluationPolicy.workspace_id == workspace_id,
                    EvaluationPolicy.enabled.is_(True),
                    EvaluationPolicy.trigger_on_config_change.is_(True),
                )
                .order_by(EvaluationPolicy.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    queued = 0
    for policy in policies:
        run = await _queue_policy_run(
            session,
            policy,
            trigger_kind="config_change",
            trigger_key=trigger_key,
            now=queued_at,
        )
        queued += int(run is not None)
    await session.commit()
    return queued


async def list_runs(
    session: AsyncSession,
    context: TenantContext,
    dataset_version_id: UUID | None,
) -> list[EvaluationRun]:
    statement = select(EvaluationRun).where(EvaluationRun.org_id == context.org_id)
    if dataset_version_id is not None:
        statement = statement.where(EvaluationRun.dataset_version_id == dataset_version_id)
    return list(
        (
            await session.scalars(
                statement.order_by(EvaluationRun.created_at.desc()).limit(100)
            )
        ).all()
    )


async def get_run_detail(
    session: AsyncSession,
    context: TenantContext,
    run_id: UUID,
) -> EvaluationRunDetail:
    run = await session.scalar(
        select(EvaluationRun).where(
            EvaluationRun.org_id == context.org_id,
            EvaluationRun.id == run_id,
        )
    )
    if run is None:
        raise NotFoundError("evaluation run not found")
    results = list(
        (
            await session.scalars(
                select(EvaluationCaseResult)
                .where(EvaluationCaseResult.run_id == run.id)
                .order_by(EvaluationCaseResult.sequence)
            )
        ).all()
    )
    payload = EvaluationRunOut.model_validate(run).model_dump()
    payload["results"] = [
        EvaluationCaseResultOut.model_validate(result).model_dump()
        for result in results
    ]
    return EvaluationRunDetail.model_validate(payload)
