"""Lease-fenced execution for bounded asynchronous document enrichment batches."""

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, cast
from uuid import UUID, uuid4

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_configured_engine, build_session_factory, naive_utc
from openrag.core.errors import OpenRAGError, UpstreamError
from openrag.modules.chat.llm import LLMStreamer
from openrag.modules.documents.authority_storage import AuthorityCollectionSpec
from openrag.modules.documents.enrichment import enrich_chunk
from openrag.modules.documents.enrichment_jobs import (
    MAX_ENRICHMENT_ATTEMPTS,
    build_enrichment_claim_query,
    schedule_enrichment_backfill_page,
)
from openrag.modules.documents.enrichment_points import (
    EnrichmentEvidence,
    build_hypothetical_question_points,
)
from openrag.modules.documents.models import (
    Document,
    DocumentChunk,
    DocumentEnrichmentJob,
    DocumentEvidenceSpan,
    DocumentVersion,
    DocumentVersionProjection,
)
from openrag.modules.embeddings.models import EmbeddingDeployment
from openrag.modules.embeddings.runtime import resolve_generation_runtime
from openrag.modules.models.models import Model
from openrag.modules.orchestration.runtime import create_model_streamer
from openrag.modules.retrieval.embeddings import DenseEmbedder, embed_sparse
from openrag.modules.tenancy.models import Workspace


class EnrichmentPointWriter(Protocol):
    async def delete(
        self,
        collection_name: str,
        *,
        points_selector: models.FilterSelector,
        wait: bool,
    ) -> object: ...

    async def upsert(
        self,
        collection_name: str,
        *,
        points: list[models.PointStruct],
        wait: bool,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class EnrichmentLeaseClaim:
    job_id: UUID
    owner: str
    token: UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class PreparedEnrichmentBatch:
    model_name: str
    streamer: LLMStreamer
    dense_embedder: DenseEmbedder
    collection: str
    evidence: tuple[EnrichmentEvidence, ...]


@dataclass(frozen=True, slots=True)
class EnrichmentBatchResult:
    generated_evidence: int
    invalid_evidence: int
    prompt_tokens: int
    completion_tokens: int
    point_count: int


@dataclass(frozen=True, slots=True)
class _PreparedClaim:
    claim: EnrichmentLeaseClaim
    batch: PreparedEnrichmentBatch


async def execute_prepared_enrichment(
    prepared: PreparedEnrichmentBatch,
    writer: EnrichmentPointWriter,
) -> EnrichmentBatchResult:
    """Run bounded provider and vector I/O with no SQL session in scope."""

    outcomes = await asyncio.gather(
        *(
            enrich_chunk(
                prepared.streamer,
                model_name=prepared.model_name,
                chunk_text=evidence.text,
            )
            for evidence in prepared.evidence
        )
    )
    questions = [
        question
        for outcome in outcomes
        for question in outcome.enrichment.hypothetical_questions
    ]
    dense_vectors: list[list[float]] = []
    sparse_vectors: list[models.SparseVector] = []
    if questions:
        dense_vectors, sparse_vectors = await asyncio.gather(
            prepared.dense_embedder.embed(questions),
            asyncio.to_thread(embed_sparse, questions),
        )
        if len(dense_vectors) != len(questions) or len(sparse_vectors) != len(
            questions
        ):
            raise UpstreamError("enrichment embedding cardinality mismatch")

    points: list[models.PointStruct] = []
    offset = 0
    generated = 0
    invalid = 0
    for evidence, outcome in zip(prepared.evidence, outcomes, strict=True):
        count = len(outcome.enrichment.hypothetical_questions)
        points.extend(
            build_hypothetical_question_points(
                evidence,
                summary=outcome.enrichment.summary,
                keywords=outcome.enrichment.keywords,
                questions=outcome.enrichment.hypothetical_questions,
                dense_vectors=dense_vectors[offset : offset + count],
                sparse_vectors=sparse_vectors[offset : offset + count],
            )
        )
        offset += count
        if outcome.status == "generated":
            generated += 1
        else:
            invalid += 1
    await writer.delete(
        prepared.collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="kind",
                        match=models.MatchValue(value="hypothetical_question"),
                    ),
                    models.FieldCondition(
                        key="evidence_span_id",
                        match=models.MatchAny(
                            any=[str(item.evidence_span_id) for item in prepared.evidence]
                        ),
                    ),
                ]
            )
        ),
        wait=True,
    )
    if points:
        await writer.upsert(
            prepared.collection,
            points=points,
            wait=True,
        )
    return EnrichmentBatchResult(
        generated_evidence=generated,
        invalid_evidence=invalid,
        prompt_tokens=sum(outcome.usage.prompt_tokens for outcome in outcomes),
        completion_tokens=sum(outcome.usage.completion_tokens for outcome in outcomes),
        point_count=len(points),
    )


def _validate_lease(owner: str, lease_seconds: int) -> None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("enrichment_lease_owner_invalid")
    if not 30 <= lease_seconds <= 600:
        raise ValueError("enrichment_lease_seconds_invalid")


async def claim_next_enrichment_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int,
) -> EnrichmentLeaseClaim | None:
    _validate_lease(owner, lease_seconds)
    now = naive_utc()
    async with session_factory.begin() as session:
        job = await session.scalar(build_enrichment_claim_query(now))
        if job is None:
            return None
        token = uuid4()
        job.status = "running"
        job.attempts += 1
        job.lease_owner = owner
        job.lease_token = token
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.started_at = job.started_at or now
        job.finished_at = None
        job.error_code = None
        return EnrichmentLeaseClaim(job.id, owner, token, job.attempts)


def _exact_evidence_text(span: DocumentEvidenceSpan, chunk: DocumentChunk) -> str:
    encoded = chunk.text.encode("utf-8")
    if (
        span.artifact_byte_start < 0
        or span.artifact_byte_end <= span.artifact_byte_start
        or span.artifact_byte_end > len(encoded)
    ):
        raise ValueError("enrichment_evidence_range_invalid")
    payload = encoded[span.artifact_byte_start : span.artifact_byte_end]
    if hashlib.sha256(payload).hexdigest() != span.content_hash:
        raise ValueError("enrichment_evidence_hash_invalid")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("enrichment_evidence_encoding_invalid") from exc
    if not text.strip():
        raise ValueError("enrichment_evidence_text_invalid")
    return text


async def _prepare_claim(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EnrichmentLeaseClaim,
    settings: Settings,
) -> _PreparedClaim | str:
    generation_id: UUID
    dense_dimension: int
    async with session_factory() as session:
        job = await session.scalar(
            select(DocumentEnrichmentJob).where(
                DocumentEnrichmentJob.id == claim.job_id,
                DocumentEnrichmentJob.status == "running",
                DocumentEnrichmentJob.lease_owner == claim.owner,
                DocumentEnrichmentJob.lease_token == claim.token,
            )
        )
        if job is None:
            return "contested"
        workspace = await session.scalar(
            select(Workspace).where(
                Workspace.id == job.workspace_id,
                Workspace.org_id == job.org_id,
                Workspace.enrichment_enabled.is_(True),
            )
        )
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == job.document_version_id,
                DocumentVersion.org_id == job.org_id,
                DocumentVersion.workspace_id == job.workspace_id,
                DocumentVersion.state == "approved",
                DocumentVersion.provenance_state == "ready",
                DocumentVersion.superseded_by_id.is_(None),
                DocumentVersion.source_deleted_at.is_(None),
            )
        )
        deployment = await session.scalar(
            select(EmbeddingDeployment).where(
                EmbeddingDeployment.id == job.embedding_deployment_id,
                EmbeddingDeployment.status == "active",
            )
        )
        model = await session.scalar(
            select(Model).where(
                Model.id == job.model_id,
                Model.probe_revision == job.model_probe_revision,
                Model.enabled.is_(True),
                Model.probe_status == "passed",
                Model.supports_chat_completion.is_(True),
                Model.supports_streaming.is_(True),
            )
        )
        if workspace is None or version is None:
            return "scope_no_longer_eligible"
        if deployment is None:
            return "embedding_generation_inactive"
        if model is None:
            return "utility_model_changed"
        document = await session.scalar(
            select(Document).where(
                Document.id == version.document_id,
                Document.org_id == job.org_id,
                Document.workspace_id == job.workspace_id,
            )
        )
        projection = await session.scalar(
            select(DocumentVersionProjection).where(
                DocumentVersionProjection.org_id == job.org_id,
                DocumentVersionProjection.workspace_id == job.workspace_id,
                DocumentVersionProjection.document_version_id == version.id,
            )
        )
        if (
            document is None
            or projection is None
            or version.source_mime is None
        ):
            return "authority_snapshot_unavailable"
        rows = list(
            (
                await session.execute(
                    select(DocumentEvidenceSpan, DocumentChunk)
                    .join(
                        DocumentChunk,
                        and_(
                            DocumentChunk.org_id == DocumentEvidenceSpan.org_id,
                            DocumentChunk.document_version_id
                            == DocumentEvidenceSpan.document_version_id,
                            DocumentChunk.id == DocumentEvidenceSpan.chunk_id,
                        ),
                    )
                    .where(
                        DocumentEvidenceSpan.org_id == job.org_id,
                        DocumentEvidenceSpan.document_version_id == version.id,
                        DocumentEvidenceSpan.ordinal >= job.evidence_start_ordinal,
                        DocumentEvidenceSpan.ordinal < job.evidence_end_ordinal,
                    )
                    .order_by(DocumentEvidenceSpan.ordinal)
                )
            ).all()
        )
        if len(rows) != job.total_evidence:
            return "evidence_batch_changed"
        evidence: list[EnrichmentEvidence] = []
        try:
            for span, chunk in rows:
                evidence.append(
                    EnrichmentEvidence(
                        org_id=job.org_id,
                        workspace_id=job.workspace_id,
                        document_id=document.id,
                        document_version_id=version.id,
                        evidence_span_id=span.id,
                        projection_revision=projection.applied_revision,
                        page_number=span.page_number,
                        ordinal=span.ordinal,
                        document_name=document.name,
                        version_label=version.version_label,
                        revision_date=version.revision_date,
                        section_path=tuple(span.section_path),
                        locator_kind=span.locator_kind,
                        locator_label=span.locator_label,
                        content_hash=span.content_hash,
                        text=_exact_evidence_text(span, chunk),
                        source_mime=version.source_mime,
                    )
                )
        except ValueError:
            return "evidence_snapshot_invalid"
        streamer = await create_model_streamer(session, model, settings)
        generation_id = deployment.generation_id
        model_name = model.litellm_model_name
        await session.rollback()

    embedding_runtime = await resolve_generation_runtime(
        session_factory,
        generation_id,
        settings,
    )
    dense_dimension = embedding_runtime.dimension
    if dense_dimension < 1:
        return "embedding_runtime_invalid"
    return _PreparedClaim(
        claim,
        PreparedEnrichmentBatch(
            model_name=model_name,
            streamer=streamer,
            dense_embedder=embedding_runtime.embedder,
            collection=AuthorityCollectionSpec(
                generation_id=generation_id,
                dense_dimension=dense_dimension,
            ).physical_collection,
            evidence=tuple(evidence),
        ),
    )


async def _set_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EnrichmentLeaseClaim,
    *,
    status: str,
    error_code: str | None,
    result: EnrichmentBatchResult | None = None,
) -> str:
    values: dict[str, object] = {
        "status": status,
        "error_code": error_code,
        "finished_at": naive_utc(),
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
    }
    if result is not None:
        values.update(
            generated_evidence=result.generated_evidence,
            invalid_evidence=result.invalid_evidence,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
    async with session_factory.begin() as session:
        updated = await session.scalar(
            update(DocumentEnrichmentJob)
            .where(
                DocumentEnrichmentJob.id == claim.job_id,
                DocumentEnrichmentJob.status == "running",
                DocumentEnrichmentJob.lease_owner == claim.owner,
                DocumentEnrichmentJob.lease_token == claim.token,
            )
            .values(**values)
            .returning(DocumentEnrichmentJob.id)
        )
        return status if updated is not None else "contested"


async def _retry_or_fail(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EnrichmentLeaseClaim,
    error_code: str,
) -> str:
    status = "queued" if claim.attempt < MAX_ENRICHMENT_ATTEMPTS else "failed"
    async with session_factory.begin() as session:
        updated = await session.scalar(
            update(DocumentEnrichmentJob)
            .where(
                DocumentEnrichmentJob.id == claim.job_id,
                DocumentEnrichmentJob.status == "running",
                DocumentEnrichmentJob.lease_owner == claim.owner,
                DocumentEnrichmentJob.lease_token == claim.token,
            )
            .values(
                status=status,
                error_code=error_code,
                finished_at=naive_utc() if status == "failed" else None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(DocumentEnrichmentJob.id)
        )
        return status if updated is not None else "contested"


async def renew_enrichment_lease(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EnrichmentLeaseClaim,
    *,
    lease_seconds: int,
) -> bool:
    _validate_lease(claim.owner, lease_seconds)
    async with session_factory.begin() as session:
        updated = await session.scalar(
            update(DocumentEnrichmentJob)
            .where(
                DocumentEnrichmentJob.id == claim.job_id,
                DocumentEnrichmentJob.status == "running",
                DocumentEnrichmentJob.lease_owner == claim.owner,
                DocumentEnrichmentJob.lease_token == claim.token,
            )
            .values(lease_expires_at=naive_utc() + timedelta(seconds=lease_seconds))
            .returning(DocumentEnrichmentJob.id)
        )
        return updated is not None


async def _heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EnrichmentLeaseClaim,
    lease_seconds: int,
) -> None:
    while True:
        await asyncio.sleep(max(10, lease_seconds // 3))
        if not await renew_enrichment_lease(
            session_factory,
            claim,
            lease_seconds=lease_seconds,
        ):
            return


async def run_enrichment_job_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    owner: str,
    writer: EnrichmentPointWriter,
) -> str:
    claim = await claim_next_enrichment_job(
        session_factory,
        owner=owner,
        lease_seconds=settings.enrichment_lease_seconds,
    )
    if claim is None:
        return "idle"
    try:
        prepared = await _prepare_claim(session_factory, claim, settings)
        if isinstance(prepared, str):
            if prepared == "contested":
                return prepared
            return await _set_terminal(
                session_factory,
                claim,
                status="skipped",
                error_code=prepared,
            )
        heartbeat = asyncio.create_task(
            _heartbeat(session_factory, claim, settings.enrichment_lease_seconds)
        )
        try:
            result = await execute_prepared_enrichment(prepared.batch, writer)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return await _set_terminal(
            session_factory,
            claim,
            status="completed",
            error_code=None,
            result=result,
        )
    except (OpenRAGError, UpstreamError):
        return await _retry_or_fail(session_factory, claim, "provider_or_config_error")
    except Exception:
        return await _retry_or_fail(session_factory, claim, "internal_error")


async def run_enrichment_worker_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> str:
    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    qdrant = AsyncQdrantClient(url=resolved.qdrant_url)
    try:
        return await run_enrichment_job_once(
            session_factory,
            resolved,
            owner=owner,
            writer=cast(EnrichmentPointWriter, qdrant),
        )
    finally:
        await qdrant.close()
        await engine.dispose()


async def run_enrichment_scheduler_once(
    *,
    settings: Settings | None = None,
) -> int:
    """Schedule one bounded backfill page without performing provider I/O."""

    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory.begin() as session:
            return await schedule_enrichment_backfill_page(session)
    finally:
        await engine.dispose()
