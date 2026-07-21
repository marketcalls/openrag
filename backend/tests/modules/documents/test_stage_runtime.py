from uuid import UUID, uuid4

import pytest
from qdrant_client import models
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.modules.documents import stage_runtime
from openrag.modules.documents.models import (
    DocumentEvidenceSpan,
    DocumentVersion,
    IngestStageAttempt,
)
from openrag.modules.documents.pipeline import IngestTransientFailure
from openrag.modules.documents.stage_runtime import run_claimed_stage_once
from openrag.modules.events.models import OutboxEvent
from openrag.modules.retrieval.embeddings import HashDenseEmbedder
from tests.modules.documents.test_models import seed_document_version, seed_scope


class MemoryStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.objects[key] = data


class RecordingQdrant:
    def __init__(self) -> None:
        self.points: list[models.PointStruct] = []
        self.collections: list[str] = []

    async def upsert(
        self,
        collection_name: str,
        *,
        points: list[models.PointStruct],
        wait: bool,
    ) -> None:
        assert wait is True
        self.collections.append(collection_name)
        self.points.extend(points)


async def sparse_embedder(texts: list[str]) -> list[models.SparseVector]:
    return [models.SparseVector(indices=[1], values=[1.0]) for _text in texts]


async def test_runtime_retries_transient_parser_initialization_failure(
    engine: AsyncEngine,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _document, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"runtime-retry-{uuid4()}",
        version_hash=f"runtime-retry-version-{uuid4()}",
        state="processing",
    )
    version.source_filename = "policy.pdf"
    version.source_mime = "application/pdf"
    attempt = IngestStageAttempt(
        org_id=organization.id,
        workspace_id=workspace.id,
        document_version_id=version.id,
        pipeline_kind="ingestion",
        stage="parse",
        checkpoint=f"parse:ingestion:1:{uuid4().hex}",
    )
    session.add(attempt)
    await session.commit()

    async def fail_parser(*args: object, **kwargs: object) -> object:
        raise IngestTransientFailure("temporary parser initialization failure")

    async def ready(candidate: UUID) -> bool:
        return True

    monkeypatch.setattr(stage_runtime, "_execute_external_stage", fail_parser)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = await run_claimed_stage_once(
        factory,
        owner="runtime-retry-worker",
        storage=MemoryStorage({version.source_storage_key: b"pdf"}),
        dense_embedder=HashDenseEmbedder(dim=3),
        sparse_embedder=sparse_embedder,
        qdrant=RecordingQdrant(),
        authority_ready=ready,
        settings=Settings(
            embedding_backend="hash",
            embedding_dim=3,
            document_stage_lease_seconds=30,
        ),
    )

    assert result == "queued"
    async with factory() as verify:
        stored_attempt = await verify.get(IngestStageAttempt, attempt.id)
        stored_version = await verify.get(DocumentVersion, version.id)
    assert stored_attempt is not None
    assert stored_attempt.state == "queued"
    assert stored_attempt.error_code == "PARSER_TRANSIENT_FAILURE"
    assert stored_version is not None
    assert stored_version.state == "processing"


async def test_runtime_executes_full_pipeline_with_fenced_postgres_completion(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"runtime-{uuid4()}",
        version_hash=f"runtime-version-{uuid4()}",
        state="processing",
    )
    version.source_filename = "policy.txt"
    version.source_mime = "text/plain"
    version.source_page_count = None
    generation_id = uuid4()
    session.add(
        IngestStageAttempt(
            org_id=organization.id,
            workspace_id=workspace.id,
            document_version_id=version.id,
            pipeline_kind="ingestion",
            stage="parse",
            checkpoint=f"parse:ingestion:1:{generation_id.hex}",
        )
    )
    await session.commit()
    storage = MemoryStorage(
        {version.source_storage_key: b"Emergency procedure.\n\nCall the HSE manager."}
    )
    qdrant = RecordingQdrant()
    readiness: list[UUID] = []

    async def ready(candidate: UUID) -> bool:
        readiness.append(candidate)
        return True

    settings = Settings(
        embedding_backend="hash",
        embedding_dim=3,
        ocr_mode="disabled",
        document_stage_lease_seconds=30,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    results = [
        await run_claimed_stage_once(
            factory,
            owner=f"runtime-worker-{index}",
            storage=storage,
            dense_embedder=HashDenseEmbedder(dim=3),
            sparse_embedder=sparse_embedder,
            qdrant=qdrant,
            authority_ready=ready,
            settings=settings,
        )
        for index in range(4)
    ]

    assert results == ["advanced", "advanced", "advanced", "completed"]
    async with factory() as verify:
        stored = await verify.get(DocumentVersion, version.id)
        evidence_count = await verify.scalar(
            select(func.count()).select_from(DocumentEvidenceSpan)
        )
        stages = list(
            (
                await verify.scalars(
                    select(IngestStageAttempt).order_by(IngestStageAttempt.created_at)
                )
            ).all()
        )
        lifecycle = await verify.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == version.id,
                OutboxEvent.event_type == "document.version.lifecycle.v1",
            )
        )

    assert stored is not None
    assert stored.state == "review"
    assert stored.provenance_state == "ready"
    assert stored.source_page_count == 1
    assert stored.lifecycle_revision == 2
    assert evidence_count is not None and evidence_count > 0
    assert [stage.stage for stage in stages] == [
        "parse",
        "chunk",
        "embed",
        "authority_upsert",
    ]
    assert all(stage.state == "succeeded" for stage in stages)
    assert lifecycle is not None
    assert readiness == [generation_id, generation_id]
    assert qdrant.collections == [f"openrag_authority_v1_{generation_id.hex}"]
    assert len(qdrant.points) == evidence_count
    assert qdrant.points[0].payload is not None
    assert qdrant.points[0].payload["document_id"] == str(document.id)
