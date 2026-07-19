import hashlib

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import (
    Document,
    DocumentAuthorityReadiness,
    DocumentBlock,
    DocumentChunk,
    DocumentChunkBlock,
    DocumentEvidenceSpan,
    DocumentVersion,
    DocumentVersionProjection,
    IngestJob,
    IngestStageAttempt,
    LegacyRebuildScanCheckpoint,
)
from openrag.modules.tenancy.models import Organization, Workspace


async def seed_scope(
    session: AsyncSession,
) -> tuple[Organization, Workspace, User]:
    organization = Organization(name="Acme")
    session.add(organization)
    await session.flush()
    workspace = Workspace(org_id=organization.id, name="Finance")
    user = User(
        org_id=organization.id,
        email="documents@acme.com",
        password_hash="x",  # noqa: S106 - inert persisted test value
    )
    session.add_all([workspace, user])
    await session.flush()
    return organization, workspace, user


async def test_document_and_job_roundtrip(session: AsyncSession) -> None:
    organization, workspace, user = await seed_scope(session)
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        filename="report.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="hash-1",
        storage_key=f"{organization.id}/{workspace.id}/document/report.pdf",
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    session.add(IngestJob(document_id=document.id, stage="parse"))
    await session.commit()

    found = (await session.execute(select(Document))).scalar_one()
    job = (await session.execute(select(IngestJob))).scalar_one()

    assert found.status == "queued"
    assert found.page_count is None
    assert job.progress == 0.0
    assert job.finished_at is None


async def test_content_hash_unique_per_workspace(session: AsyncSession) -> None:
    organization, workspace, user = await seed_scope(session)
    fields = {
        "org_id": organization.id,
        "workspace_id": workspace.id,
        "filename": "report.pdf",
        "mime": "application/pdf",
        "size_bytes": 10,
        "content_hash": "duplicate",
        "storage_key": "object-key",
        "created_by": user.id,
    }
    session.add(Document(**fields))
    await session.commit()
    session.add(Document(**fields))

    with pytest.raises(IntegrityError):
        await session.commit()


async def seed_document_version(
    session: AsyncSession,
    *,
    organization: Organization,
    workspace: Workspace,
    user: User,
    document_hash: str,
    version_hash: str,
    sequence: int = 1,
    state: str = "draft",
) -> tuple[Document, DocumentVersion]:
    if len(version_hash) != 64:
        version_hash = hashlib.sha256(version_hash.encode()).hexdigest()
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        filename=f"{document_hash}.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash=document_hash,
        storage_key=f"{organization.id}/{workspace.id}/{document_hash}.pdf",
        owner_id=user.id,
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        org_id=organization.id,
        workspace_id=workspace.id,
        document_id=document.id,
        sequence=sequence,
        version_label=f"Rev {sequence}",
        version_key=f"rev {sequence}",
        content_hash=version_hash,
        state=state,
        created_by=user.id,
    )
    session.add(version)
    await session.flush()
    return document, version


async def test_chunk_block_membership_cannot_cross_version(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, first = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="doc-one",
        version_hash="version-one",
    )
    _, second = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="doc-two",
        version_hash="version-two",
    )
    chunk = DocumentChunk(
        org_id=organization.id,
        document_version_id=first.id,
        ordinal=0,
        text="chunk",
        token_count=1,
    )
    block = DocumentBlock(
        org_id=organization.id,
        document_version_id=second.id,
        ordinal=0,
        text="block",
    )
    session.add_all([chunk, block])
    await session.flush()
    session.add(
        DocumentChunkBlock(
            org_id=organization.id,
            document_version_id=first.id,
            chunk_id=chunk.id,
            block_id=block.id,
            position=0,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_parent_chunk_cannot_cross_versions(session: AsyncSession) -> None:
    organization, workspace, user = await seed_scope(session)
    _, first = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="parent-doc-one",
        version_hash="parent-version-one",
    )
    _, second = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="parent-doc-two",
        version_hash="parent-version-two",
    )
    foreign_parent = DocumentChunk(
        org_id=organization.id,
        document_version_id=second.id,
        ordinal=0,
        text="foreign parent",
        token_count=2,
    )
    session.add(foreign_parent)
    await session.flush()
    session.add(
        DocumentChunk(
            org_id=organization.id,
            document_version_id=first.id,
            parent_chunk_id=foreign_parent.id,
            ordinal=0,
            text="child",
            token_count=1,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_supersession_cannot_cross_documents(session: AsyncSession) -> None:
    organization, workspace, user = await seed_scope(session)
    _, approved = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="approved-doc",
        version_hash="approved-version",
        state="approved",
    )
    _, foreign_successor = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="foreign-doc",
        version_hash="foreign-version",
        state="review",
    )
    approved.state = "superseded"
    approved.superseded_by_id = foreign_successor.id
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("document", "owner_id"),
        ("document", "created_by"),
        ("version", "created_by"),
        ("version", "approved_by"),
        ("version", "rejected_by"),
        ("version", "obsolete_by"),
    ],
)
async def test_document_actors_cannot_cross_organizations(
    session: AsyncSession,
    target: str,
    field: str,
) -> None:
    organization, workspace, user = await seed_scope(session)
    foreign_org = Organization(name=f"Foreign {target} {field}")
    session.add(foreign_org)
    await session.flush()
    foreign_user = User(
        org_id=foreign_org.id,
        email=f"{target}-{field}@foreign.example",
        password_hash="x",  # noqa: S106 - inert persisted test value
    )
    session.add(foreign_user)
    await session.flush()

    document, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"actor-{target}-{field}",
        version_hash=f"actor-version-{target}-{field}",
    )
    setattr(document if target == "document" else version, field, foreign_user.id)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_page_local_evidence_ordinal_is_positive(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="evidence-doc",
        version_hash="evidence-version",
    )
    chunk = DocumentChunk(
        org_id=organization.id,
        document_version_id=version.id,
        ordinal=0,
        text="evidence",
        token_count=1,
    )
    session.add(chunk)
    await session.flush()
    session.add(
        DocumentEvidenceSpan(
            org_id=organization.id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            page_number=0,
            locator_kind="page",
            locator_label="0",
            section_path=["Summary"],
            content_hash="0" * 64,
            ordinal=0,
            token_count=1,
            artifact_byte_start=0,
            artifact_byte_end=8,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_logical_document_retains_distinct_immutable_version_sources(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        name="Emergency Response Plan",
        department="HSE",
        document_type="controlled-procedure",
        external_identifier="ERP-001",
        acl_policy={"mode": "workspace", "roles": ["hse-manager"]},
        filename="legacy-mirror.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="logical-document-mirror",
        storage_key="legacy/mirror.pdf",
        owner_id=user.id,
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    common = {
        "org_id": organization.id,
        "workspace_id": workspace.id,
        "document_id": document.id,
        "created_by": user.id,
        "parser_profile_version": "docling-2",
        "ocr_profile_version": "ocr-v1",
        "chunking_profile_version": "semantic-v2",
        "embedding_profile_version": "bge-m3-v1",
        "index_profile_version": "hybrid-v3",
    }
    first = DocumentVersion(
        **common,
        sequence=1,
        version_label="Rev 1",
        version_key="rev 1",
        content_hash="1" * 64,
        source_filename="erp-r1.pdf",
        source_mime="application/pdf",
        source_size_bytes=101,
        source_storage_key="versions/r1/source",
        source_page_count=2,
    )
    second = DocumentVersion(
        **common,
        sequence=2,
        version_label="Rev 2",
        version_key="rev 2",
        content_hash="2" * 64,
        source_filename="erp-r2.docx",
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_size_bytes=202,
        source_storage_key="versions/r2/source",
        source_page_count=3,
    )
    session.add_all([first, second])
    await session.commit()

    assert document.name == "Emergency Response Plan"
    assert first.source_storage_key == "versions/r1/source"
    assert second.source_storage_key == "versions/r2/source"
    assert first.source_mime != second.source_mime
    assert first.parser_profile_version == second.parser_profile_version


def test_block_chunk_and_later_consumer_shapes_are_complete() -> None:
    block_columns = set(inspect(DocumentBlock).columns.keys())
    chunk_columns = set(inspect(DocumentChunk).columns.keys())
    projection_columns = set(inspect(DocumentVersionProjection).columns.keys())
    readiness_columns = set(inspect(DocumentAuthorityReadiness).columns.keys())
    scanner_columns = set(inspect(LegacyRebuildScanCheckpoint).columns.keys())

    assert {
        "page_number",
        "locator_kind",
        "locator_label",
        "block_type",
        "section_path",
        "extraction_method",
        "ocr_profile_version",
        "content_hash",
    } <= block_columns
    assert {
        "page_start",
        "page_end",
        "section_path",
        "content_hash",
        "chunking_profile_version",
        "embedding_profile_version",
    } <= chunk_columns
    assert {"applied_revision", "applied_at"} <= projection_columns
    assert {"grounding_policy_version", "blocker_codes"} <= readiness_columns
    assert "cursor_document_version_id" in scanner_columns


async def test_projection_cannot_bind_version_from_peer_workspace(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    peer_workspace = Workspace(org_id=organization.id, name="Peer")
    session.add(peer_workspace)
    await session.flush()
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="projection-scope",
        version_hash="3" * 64,
    )
    session.add(
        DocumentVersionProjection(
            org_id=organization.id,
            workspace_id=peer_workspace.id,
            document_version_id=version.id,
            is_current_eligible=True,
            applied_revision=1,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_stage_attempt_cannot_bind_version_from_peer_workspace(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    peer_workspace = Workspace(org_id=organization.id, name="Peer Stage")
    session.add(peer_workspace)
    await session.flush()
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="stage-scope",
        version_hash="4" * 64,
    )
    session.add(
        IngestStageAttempt(
            org_id=organization.id,
            workspace_id=peer_workspace.id,
            document_version_id=version.id,
            pipeline_kind="ingest",
            stage="parse",
            checkpoint="initial",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"content_hash": "short"},
        {"source_size_bytes": -1},
        {"source_page_count": 0},
        {"parser_profile_version": ""},
    ],
)
async def test_version_source_digest_and_profile_bounds_reject_invalid_rows(
    session: AsyncSession,
    invalid_values: dict[str, object],
) -> None:
    organization, workspace, user = await seed_scope(session)
    document, _ = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="bounds-source",
        version_hash="5" * 64,
    )
    values: dict[str, object] = {
        "org_id": organization.id,
        "workspace_id": workspace.id,
        "document_id": document.id,
        "sequence": 2,
        "version_label": "Rev 2",
        "version_key": "rev 2",
        "content_hash": "6" * 64,
        "source_filename": "source.pdf",
        "source_mime": "application/pdf",
        "source_size_bytes": 10,
        "source_storage_key": "versions/2/source",
        "source_page_count": 1,
        "parser_profile_version": "docling-v1",
        "ocr_profile_version": "ocr-v1",
        "chunking_profile_version": "chunk-v1",
        "embedding_profile_version": "embedding-v1",
        "index_profile_version": "index-v1",
        "created_by": user.id,
    }
    values.update(invalid_values)
    session.add(DocumentVersion(**values))
    with pytest.raises(DBAPIError):
        await session.commit()
