"""Transactional scheduling and lease selection for asynchronous enrichment."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.models import (
    DocumentEnrichmentJob,
    DocumentEvidenceSpan,
    DocumentVersion,
)
from openrag.modules.embeddings.models import EmbeddingDeployment
from openrag.modules.models.models import Model
from openrag.modules.models.utility import resolve_utility_model
from openrag.modules.tenancy.models import Workspace

PROMPT_CONTRACT_VERSION = "chunk-enrichment-v1"
MAX_ENRICHMENT_ATTEMPTS = 8
ENRICHMENT_BATCH_SIZE = 8
ENRICHMENT_SCHEDULE_VERSION_LIMIT = 25
EnrichmentSource = Literal["approval", "backfill", "reindex"]


def build_enrichment_claim_query(
    now: datetime,
) -> Select[tuple[DocumentEnrichmentJob]]:
    return (
        select(DocumentEnrichmentJob)
        .where(
            DocumentEnrichmentJob.attempts < MAX_ENRICHMENT_ATTEMPTS,
            or_(
                DocumentEnrichmentJob.status == "queued",
                and_(
                    DocumentEnrichmentJob.status == "running",
                    DocumentEnrichmentJob.lease_expires_at.is_not(None),
                    DocumentEnrichmentJob.lease_expires_at <= now,
                ),
            ),
        )
        .order_by(DocumentEnrichmentJob.created_at, DocumentEnrichmentJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def resolve_enrichment_prerequisites(
    session: AsyncSession,
) -> tuple[Model, EmbeddingDeployment] | None:
    model = await resolve_utility_model(session)
    if model is None:
        return None
    deployment = await session.scalar(
        select(EmbeddingDeployment).where(EmbeddingDeployment.status == "active")
    )
    if deployment is None:
        return None
    return model, deployment


def build_enrichment_workspace_page_query(
    *,
    model_id: UUID,
    model_probe_revision: int,
    embedding_deployment_id: UUID,
) -> Select[tuple[UUID, UUID]]:
    """Select only workspaces with unscheduled eligible evidence."""

    already_scheduled = (
        select(DocumentEnrichmentJob.id)
        .where(
            DocumentEnrichmentJob.document_version_id == DocumentVersion.id,
            DocumentEnrichmentJob.embedding_deployment_id
            == embedding_deployment_id,
            DocumentEnrichmentJob.model_id == model_id,
            DocumentEnrichmentJob.model_probe_revision == model_probe_revision,
            DocumentEnrichmentJob.prompt_contract_version
            == PROMPT_CONTRACT_VERSION,
        )
        .exists()
    )
    eligible_version = (
        select(DocumentVersion.id)
        .join(
            DocumentEvidenceSpan,
            and_(
                DocumentEvidenceSpan.org_id == DocumentVersion.org_id,
                DocumentEvidenceSpan.document_version_id == DocumentVersion.id,
            ),
        )
        .where(
            DocumentVersion.org_id == Workspace.org_id,
            DocumentVersion.workspace_id == Workspace.id,
            DocumentVersion.state == "approved",
            DocumentVersion.provenance_state == "ready",
            DocumentVersion.superseded_by_id.is_(None),
            DocumentVersion.source_deleted_at.is_(None),
            ~already_scheduled,
        )
        .correlate(Workspace)
        .exists()
    )
    return (
        select(Workspace.org_id, Workspace.id)
        .where(Workspace.enrichment_enabled.is_(True), eligible_version)
        .order_by(Workspace.org_id, Workspace.id)
        .limit(100)
    )


async def enqueue_enrichment_jobs(
    session: AsyncSession,
    *,
    org_id: UUID,
    workspace_id: UUID,
    requested_by: UUID | None,
    source: EnrichmentSource,
    document_version_ids: tuple[UUID, ...] | None = None,
) -> int:
    """Insert idempotent content-free jobs in the caller's transaction."""

    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_id,
            Workspace.enrichment_enabled.is_(True),
        )
    )
    if workspace is None:
        return 0
    prerequisites = await resolve_enrichment_prerequisites(session)
    if prerequisites is None:
        return 0
    model, deployment = prerequisites
    statement = (
        select(DocumentVersion.id, func.count(DocumentEvidenceSpan.id))
        .join(
            DocumentEvidenceSpan,
            and_(
                DocumentEvidenceSpan.org_id == DocumentVersion.org_id,
                DocumentEvidenceSpan.document_version_id == DocumentVersion.id,
            ),
        )
        .where(
            DocumentVersion.org_id == org_id,
            DocumentVersion.workspace_id == workspace_id,
            DocumentVersion.state == "approved",
            DocumentVersion.provenance_state == "ready",
            DocumentVersion.superseded_by_id.is_(None),
            DocumentVersion.source_deleted_at.is_(None),
            ~select(DocumentEnrichmentJob.id)
            .where(
                DocumentEnrichmentJob.document_version_id == DocumentVersion.id,
                DocumentEnrichmentJob.embedding_deployment_id == deployment.id,
                DocumentEnrichmentJob.model_id == model.id,
                DocumentEnrichmentJob.model_probe_revision == model.probe_revision,
                DocumentEnrichmentJob.prompt_contract_version == PROMPT_CONTRACT_VERSION,
            )
            .exists(),
        )
        .group_by(DocumentVersion.id)
    )
    if document_version_ids is not None:
        if not document_version_ids:
            return 0
        statement = statement.where(DocumentVersion.id.in_(document_version_ids))
    versions = list(
        (
            await session.execute(
                statement.order_by(DocumentVersion.id).limit(ENRICHMENT_SCHEDULE_VERSION_LIMIT)
            )
        ).all()
    )
    if not versions:
        return 0

    inserted_count = 0
    for version_id, evidence_count in versions:
        values = [
            {
                "id": uuid4(),
                "org_id": org_id,
                "workspace_id": workspace_id,
                "document_version_id": version_id,
                "embedding_deployment_id": deployment.id,
                "model_id": model.id,
                "model_probe_revision": model.probe_revision,
                "prompt_contract_version": PROMPT_CONTRACT_VERSION,
                "evidence_start_ordinal": start,
                "evidence_end_ordinal": min(
                    start + ENRICHMENT_BATCH_SIZE,
                    evidence_count,
                ),
                "total_evidence": min(
                    ENRICHMENT_BATCH_SIZE,
                    evidence_count - start,
                ),
                "source": source,
                "status": "queued",
                "attempts": 0,
                "requested_by": requested_by,
            }
            for start in range(0, evidence_count, ENRICHMENT_BATCH_SIZE)
        ]
        inserted = await session.scalars(
            insert(DocumentEnrichmentJob)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_document_enrichment_jobs_generation")
            .returning(DocumentEnrichmentJob.id)
        )
        inserted_count += len(list(inserted))
    return inserted_count


async def schedule_enrichment_backfill_page(
    session: AsyncSession,
    *,
    requested_by: UUID | None = None,
) -> int:
    """Schedule one bounded page; repeated ticks eventually cover the corpus."""

    prerequisites = await resolve_enrichment_prerequisites(session)
    if prerequisites is None:
        return 0
    model, deployment = prerequisites
    workspaces = list(
        (
            await session.execute(
                build_enrichment_workspace_page_query(
                    model_id=model.id,
                    model_probe_revision=model.probe_revision,
                    embedding_deployment_id=deployment.id,
                )
            )
        ).all()
    )
    scheduled = 0
    for org_id, workspace_id in workspaces:
        scheduled += await enqueue_enrichment_jobs(
            session,
            org_id=org_id,
            workspace_id=workspace_id,
            requested_by=requested_by,
            source="backfill",
        )
    return scheduled
