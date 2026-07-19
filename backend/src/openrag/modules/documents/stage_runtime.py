"""Runtime composition for lease-fenced durable document stages."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.core.storage import ObjectStorage, build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.authority_storage import (
    AuthorityCollectionSpec,
    probe_authority_storage,
)
from openrag.modules.documents.lifecycle import (
    DocumentVersionState,
    ensure_transition,
)
from openrag.modules.documents.models import (
    Document,
    DocumentEvidenceSpan,
    DocumentVersion,
    DocumentVersionProjection,
    IngestStageAttempt,
)
from openrag.modules.documents.pipeline import IngestFailure, ParseProfile
from openrag.modules.documents.provenance import persist_page_provenance
from openrag.modules.documents.stage_adapters import (
    AuthorityPlan,
    AuthorityPointWriter,
    AuthorityReady,
    AuthorityStorageUnavailable,
    ChunkStageResult,
    PersistedEvidence,
    SparseEmbedder,
    StageObjectStorage,
    StageSourcePlan,
    authority_upsert_external,
    chunk_stage_external,
    embed_stage_external,
    parse_stage_external,
)
from openrag.modules.documents.stages import (
    StageCheckpoint,
    StageClaim,
    StageResultApplier,
    claim_stage,
    complete_stage,
    heartbeat_stage,
    parse_stage_checkpoint,
    retry_stage,
)
from openrag.modules.embeddings.runtime import (
    EmbeddingRuntime,
    resolve_generation_runtime,
)
from openrag.modules.events.envelopes import DocumentVersionLifecycleV1
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.retrieval.client import get_qdrant
from openrag.modules.retrieval.embeddings import DenseEmbedder

_logger = logging.getLogger(__name__)


class StageLeaseLost(RuntimeError):
    """The current worker no longer owns the stage completion fence."""


@dataclass(frozen=True, slots=True)
class StageExecutionPlan:
    source: StageSourcePlan
    parent_digests: dict[str, str]
    authority: AuthorityPlan | None


@dataclass(frozen=True, slots=True)
class StageCompletion:
    output_digest: str
    apply_result: StageResultApplier | None = None


def _parse_profile(settings: Settings) -> ParseProfile:
    return ParseProfile(
        max_file_bytes=settings.max_upload_mb * 1024 * 1024,
        max_pages=settings.parser_max_pages,
        max_page_pixels=settings.parser_max_page_pixels,
        render_dpi=settings.parser_render_dpi,
        timeout_seconds=settings.parser_timeout_seconds,
        max_blocks=settings.parser_max_blocks,
        max_output_chars=settings.parser_max_output_chars,
        ocr_mode=settings.ocr_mode,
        ocr_languages=tuple(
            language.strip()
            for language in settings.ocr_languages.split(",")
            if language.strip()
        ),
        ocr_min_confidence=settings.ocr_min_confidence,
        ocr_text_score=settings.ocr_text_score,
        ocr_bitmap_area_threshold=settings.ocr_bitmap_area_threshold,
        ocr_batch_size=settings.ocr_batch_size,
    )


def _version_is_active(pipeline_kind: str, version: DocumentVersion) -> bool:
    if (
        version.source_delete_requested_at is not None
        or version.source_deleted_at is not None
    ):
        return False
    if pipeline_kind == "ingestion":
        return version.state == "processing" and version.provenance_state == "building"
    return (
        pipeline_kind == "rebuild"
        and version.state == "approved"
        and version.provenance_state == "building"
    )


def _claim_accepts_version(claim: StageClaim, version: DocumentVersion) -> bool:
    return (
        version.id == claim.document_version_id
        and version.org_id == claim.org_id
        and version.workspace_id == claim.workspace_id
        and _version_is_active(claim.pipeline_kind, version)
    )


async def _required_parent_digest(
    session: AsyncSession,
    claim: StageClaim,
    checkpoint: StageCheckpoint,
    stage: str,
) -> str:
    digest = await session.scalar(
        select(IngestStageAttempt.output_digest).where(
            IngestStageAttempt.org_id == claim.org_id,
            IngestStageAttempt.workspace_id == claim.workspace_id,
            IngestStageAttempt.document_version_id == claim.document_version_id,
            IngestStageAttempt.pipeline_kind == claim.pipeline_kind,
            IngestStageAttempt.stage == stage,
            IngestStageAttempt.checkpoint == checkpoint.for_stage(stage),
            IngestStageAttempt.state == "succeeded",
        )
    )
    if not isinstance(digest, str):
        raise IngestFailure("parent stage output is unavailable")
    return digest


async def load_stage_execution_plan(
    session_factory: async_sessionmaker[AsyncSession],
    claim: StageClaim,
    *,
    dense_dimension: int,
) -> StageExecutionPlan:
    """Load a content-free execution plan in one short read transaction."""

    checkpoint = parse_stage_checkpoint(claim.checkpoint)
    async with session_factory() as session:
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == claim.document_version_id,
                DocumentVersion.org_id == claim.org_id,
                DocumentVersion.workspace_id == claim.workspace_id,
            )
        )
        if version is None or not _claim_accepts_version(claim, version):
            raise IngestFailure("stage version is no longer active")
        document = await session.scalar(
            select(Document).where(
                Document.id == version.document_id,
                Document.org_id == claim.org_id,
                Document.workspace_id == claim.workspace_id,
            )
        )
        if (
            document is None
            or version.source_storage_key is None
            or version.source_filename is None
            or version.source_mime is None
        ):
            raise IngestFailure("stage source identity is incomplete")
        try:
            source = StageSourcePlan(
                org_id=version.org_id,
                workspace_id=version.workspace_id,
                document_version_id=version.id,
                source_storage_key=version.source_storage_key,
                source_filename=version.source_filename,
                source_mime=version.source_mime,
                embedding_profile_version=version.embedding_profile_version,
                dense_dimension=dense_dimension,
            )
        except ValueError as exc:
            raise IngestFailure("stage source identity is invalid") from exc

        parent_digests: dict[str, str] = {}
        if claim.stage in {"chunk", "embed", "authority_upsert"}:
            parent_digests["parse"] = await _required_parent_digest(
                session, claim, checkpoint, "parse"
            )
        if claim.stage in {"embed", "authority_upsert"}:
            parent_digests["chunk"] = await _required_parent_digest(
                session, claim, checkpoint, "chunk"
            )
        if claim.stage == "authority_upsert":
            parent_digests["embed"] = await _required_parent_digest(
                session, claim, checkpoint, "embed"
            )

        authority: AuthorityPlan | None = None
        if claim.stage == "authority_upsert":
            evidence = list(
                (
                    await session.scalars(
                        select(DocumentEvidenceSpan)
                        .where(
                            DocumentEvidenceSpan.org_id == claim.org_id,
                            DocumentEvidenceSpan.document_version_id
                            == claim.document_version_id,
                        )
                        .order_by(DocumentEvidenceSpan.ordinal)
                    )
                ).all()
            )
            projection = await session.scalar(
                select(DocumentVersionProjection).where(
                    DocumentVersionProjection.org_id == claim.org_id,
                    DocumentVersionProjection.workspace_id == claim.workspace_id,
                    DocumentVersionProjection.document_version_id == version.id,
                )
            )
            try:
                authority = AuthorityPlan(
                    source=source,
                    document_id=document.id,
                    document_name=document.name,
                    version_label=version.version_label,
                    revision_date=version.revision_date,
                    projection_revision=(
                        projection.applied_revision if projection is not None else 0
                    ),
                    evidence=[
                        PersistedEvidence(
                            id=row.id,
                            ordinal=row.ordinal,
                            page_number=row.page_number,
                            locator_kind=row.locator_kind,
                            locator_label=row.locator_label,
                            section_path=tuple(row.section_path),
                            content_hash=row.content_hash,
                        )
                        for row in evidence
                    ],
                )
            except ValueError as exc:
                raise IngestFailure("authority plan is invalid") from exc
        return StageExecutionPlan(
            source=source,
            parent_digests=parent_digests,
            authority=authority,
        )


async def _locked_active_version(
    session: AsyncSession,
    row: IngestStageAttempt,
    source: StageSourcePlan,
) -> DocumentVersion:
    version = await session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == row.document_version_id,
            DocumentVersion.org_id == row.org_id,
            DocumentVersion.workspace_id == row.workspace_id,
        )
        .with_for_update()
    )
    if (
        version is None
        or version.source_storage_key != source.source_storage_key
        or version.source_filename != source.source_filename
        or version.source_mime != source.source_mime
        or not _version_is_active(row.pipeline_kind, version)
    ):
        raise IngestFailure("stage version changed before completion")
    return version


async def _locked_document(
    session: AsyncSession,
    version: DocumentVersion,
) -> Document:
    document = await session.scalar(
        select(Document)
        .where(
            Document.id == version.document_id,
            Document.org_id == version.org_id,
            Document.workspace_id == version.workspace_id,
        )
        .with_for_update()
    )
    if document is None:
        raise IngestFailure("stage document changed before completion")
    return document


def _parse_applier(
    source: StageSourcePlan,
    page_count: int,
) -> StageResultApplier:
    async def apply(
        session: AsyncSession,
        row: IngestStageAttempt,
        _checkpoint: StageCheckpoint,
    ) -> None:
        version = await _locked_active_version(session, row, source)
        document = await _locked_document(session, version)
        version.source_page_count = page_count
        document.page_count = page_count
        document.status = "processing"
        document.error = None

    return apply


def _chunk_applier(
    source: StageSourcePlan,
    result: ChunkStageResult,
) -> StageResultApplier:
    async def apply(
        session: AsyncSession,
        row: IngestStageAttempt,
        _checkpoint: StageCheckpoint,
    ) -> None:
        version = await _locked_active_version(session, row, source)
        if version.source_page_count != result.parsed.page_count:
            raise IngestFailure("parsed page count changed before chunk completion")
        await persist_page_provenance(
            session,
            version,
            result.parsed.blocks,
            result.chunks,
            result.evidence_spans,
        )

    return apply


def _authority_applier(source: StageSourcePlan) -> StageResultApplier:
    async def apply(
        session: AsyncSession,
        row: IngestStageAttempt,
        _checkpoint: StageCheckpoint,
    ) -> None:
        version = await _locked_active_version(session, row, source)
        document = await _locked_document(session, version)
        version.processing_error_code = None
        if row.pipeline_kind == "ingestion":
            previous_state = version.state
            ensure_transition(previous_state, DocumentVersionState.REVIEW)
            version.state = DocumentVersionState.REVIEW.value
            version.provenance_state = "ready"
            version.lifecycle_revision += 1
            occurred_at = datetime.now(UTC)
            add_registered_event(
                session,
                payload=DocumentVersionLifecycleV1(
                    document_id=version.document_id,
                    previous_state=DocumentVersionState(previous_state),
                    new_state=DocumentVersionState.REVIEW,
                ),
                org_id=version.org_id,
                workspace_id=version.workspace_id,
                aggregate_id=version.id,
                lifecycle_revision=version.lifecycle_revision,
                occurred_at=occurred_at,
            )
            document.status = "review"
            document.error = None
            action = "document.version.review"
        else:
            version.provenance_state = "ready"
            document.status = "indexed"
            document.error = None
            action = "document.version.rebuilt"
        await record_audit(
            session,
            org_id=version.org_id,
            actor_id=version.created_by,
            action=action,
            target_type="document_version",
            target_id=str(version.id),
        )

    return apply


async def _execute_external_stage(
    claim: StageClaim,
    plan: StageExecutionPlan,
    *,
    storage: StageObjectStorage,
    dense_embedder: DenseEmbedder,
    sparse_embedder: SparseEmbedder | None,
    qdrant: AuthorityPointWriter,
    authority_ready: AuthorityReady,
    settings: Settings,
) -> StageCompletion:
    if claim.stage == "parse":
        parsed_result = await parse_stage_external(
            claim,
            plan.source,
            storage,
            _parse_profile(settings),
        )
        return StageCompletion(
            output_digest=parsed_result.artifact.digest,
            apply_result=_parse_applier(
                plan.source,
                parsed_result.parsed.page_count,
            ),
        )
    if claim.stage == "chunk":
        chunk_result = await chunk_stage_external(
            claim,
            plan.source,
            storage,
            parsed_digest=plan.parent_digests["parse"],
        )
        return StageCompletion(
            output_digest=chunk_result.artifact.digest,
            apply_result=_chunk_applier(plan.source, chunk_result),
        )
    if claim.stage == "embed":
        if sparse_embedder is None:
            embed_result = await embed_stage_external(
                claim,
                plan.source,
                storage,
                chunks_digest=plan.parent_digests["chunk"],
                dense_embedder=dense_embedder,
            )
        else:
            embed_result = await embed_stage_external(
                claim,
                plan.source,
                storage,
                chunks_digest=plan.parent_digests["chunk"],
                dense_embedder=dense_embedder,
                sparse_embedder=sparse_embedder,
            )
        return StageCompletion(output_digest=embed_result.artifact.digest)
    if claim.stage == "authority_upsert" and plan.authority is not None:
        authority_result = await authority_upsert_external(
            claim,
            plan.authority,
            storage,
            chunks_digest=plan.parent_digests["chunk"],
            vectors_digest=plan.parent_digests["embed"],
            authority_ready=authority_ready,
            qdrant=qdrant,
        )
        return StageCompletion(
            output_digest=authority_result.output_digest,
            apply_result=_authority_applier(plan.source),
        )
    raise IngestFailure("stage execution plan is incomplete")


async def _with_heartbeat[T](
    session_factory: async_sessionmaker[AsyncSession],
    claim: StageClaim,
    work: Coroutine[Any, Any, T],
    *,
    lease_seconds: int,
) -> T:
    task: asyncio.Task[T] = asyncio.create_task(work)
    interval = max(1.0, lease_seconds / 3)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=interval)
            if done:
                return task.result()
            if not await heartbeat_stage(
                session_factory,
                claim,
                lease_seconds=lease_seconds,
            ):
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise StageLeaseLost("stage lease was lost during external work")
    finally:
        if not task.done():
            task.cancel()


def _terminal_error_code(stage: str) -> str:
    return {
        "parse": "PARSE_INPUT_INVALID",
        "chunk": "CHUNK_OUTPUT_INVALID",
        "embed": "EMBEDDING_OUTPUT_INVALID",
        "authority_upsert": "AUTHORITY_OUTPUT_INVALID",
    }.get(stage, "STAGE_OUTPUT_INVALID")


async def run_claimed_stage_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    storage: StageObjectStorage,
    dense_embedder: DenseEmbedder | None = None,
    embedding_runtime_resolver: Callable[[UUID], Awaitable[EmbeddingRuntime]] | None = None,
    sparse_embedder: SparseEmbedder | None = None,
    qdrant: AuthorityPointWriter,
    authority_ready: AuthorityReady,
    settings: Settings,
) -> str:
    """Claim and execute at most one stage without holding SQL across I/O."""

    lease_seconds = settings.document_stage_lease_seconds
    claim = await claim_stage(
        session_factory,
        owner=owner,
        lease_seconds=lease_seconds,
        authority_ready=authority_ready,
    )
    if claim is None:
        return "idle"
    try:
        if embedding_runtime_resolver is not None:
            embedding_runtime = await embedding_runtime_resolver(
                claim.authority_generation_id
            )
        elif dense_embedder is not None:
            embedding_runtime = EmbeddingRuntime(
                embedder=dense_embedder,
                dimension=settings.embedding_dim,
                profile_version="configured-test-runtime",
            )
        else:
            raise IngestFailure("embedding runtime is unavailable")
        plan = await load_stage_execution_plan(
            session_factory,
            claim,
            dense_dimension=embedding_runtime.dimension,
        )
        completion = await _with_heartbeat(
            session_factory,
            claim,
            _execute_external_stage(
                claim,
                plan,
                storage=storage,
                dense_embedder=embedding_runtime.embedder,
                sparse_embedder=sparse_embedder,
                qdrant=qdrant,
                authority_ready=authority_ready,
                settings=settings,
            ),
            lease_seconds=lease_seconds,
        )
        return await complete_stage(
            session_factory,
            claim,
            output_digest=completion.output_digest,
            apply_result=completion.apply_result,
        )
    except StageLeaseLost:
        return "lease_lost"
    except AuthorityStorageUnavailable:
        return await retry_stage(
            session_factory,
            claim,
            error_code="AUTHORITY_STORAGE_NOT_READY",
        )
    except IngestFailure:
        _logger.warning(
            "durable document stage rejected terminal output",
            extra={"stage": claim.stage, "attempt_id": str(claim.attempt_id)},
        )
        return await retry_stage(
            session_factory,
            claim,
            error_code=_terminal_error_code(claim.stage),
            terminal=True,
        )
    except Exception:
        _logger.exception(
            "durable document stage transient failure",
            extra={"stage": claim.stage, "attempt_id": str(claim.attempt_id)},
        )
        return await retry_stage(
            session_factory,
            claim,
            error_code="STAGE_TRANSIENT_FAILURE",
        )


async def run_durable_stage_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> str:
    """Compose production dependencies for one bounded durable-stage tick."""

    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    storage: ObjectStorage = build_storage(resolved)
    qdrant: AsyncQdrantClient = get_qdrant()

    async def runtime(generation_id: UUID) -> EmbeddingRuntime:
        return await resolve_generation_runtime(
            session_factory,
            generation_id,
            resolved,
        )

    async def ready(generation_id: UUID) -> bool:
        embedding_runtime = await runtime(generation_id)
        status = await probe_authority_storage(
            AuthorityCollectionSpec(
                generation_id=generation_id,
                dense_dimension=embedding_runtime.dimension,
            ),
            client=qdrant,
        )
        return status.ready

    try:
        return await run_claimed_stage_once(
            session_factory,
            owner=owner,
            storage=cast(StageObjectStorage, storage),
            embedding_runtime_resolver=runtime,
            sparse_embedder=None,
            qdrant=cast(AuthorityPointWriter, qdrant),
            authority_ready=ready,
            settings=resolved,
        )
    finally:
        await qdrant.close()
        await engine.dispose()
