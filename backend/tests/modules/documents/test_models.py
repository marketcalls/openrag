import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import insert, inspect, select
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
    DocumentVersionDecisionRecord,
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
        name="report.pdf",
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


def test_legacy_mirrors_match_task_two_expand_target() -> None:
    document_table = Document.__table__
    for name in (
        "filename",
        "mime",
        "size_bytes",
        "content_hash",
        "storage_key",
        "page_count",
        "status",
        "error",
    ):
        assert document_table.c[name].nullable is True
    assert document_table.c["name"].nullable is False
    assert IngestJob.__table__.c.document_id.nullable is True
    assert "uq_documents_workspace_hash" not in {
        constraint.name for constraint in document_table.constraints
    }


def test_version_decision_model_declares_exact_scope_and_append_ledger_shape() -> None:
    table = DocumentVersionDecisionRecord.__table__
    assert set(table.c.keys()) >= {
        "org_id",
        "workspace_id",
        "document_id",
        "document_version_id",
        "lifecycle_revision",
        "decision",
        "actor_id",
        "reason",
        "id",
        "created_at",
    }
    assert table.c.reason.type.length == 500
    assert table.c.reason.nullable is True
    constraints = {constraint.name: constraint for constraint in table.constraints}
    assert set(constraints) >= {
        "uq_document_version_decision_records_version_revision",
        "ck_document_version_decision_records_revision_positive",
        "ck_document_version_decision_records_decision",
        "ck_document_version_decision_records_reason_bounded",
        "fk_document_version_decision_records_exact_version",
        "fk_document_version_decision_records_org_actor",
    }
    assert str(
        constraints["ck_document_version_decision_records_reason_bounded"].sqltext
    ) == "reason IS NULL OR char_length(btrim(reason)) BETWEEN 1 AND 500"
    assert [
        column.name
        for column in constraints[
            "fk_document_version_decision_records_exact_version"
        ].columns
    ] == ["org_id", "workspace_id", "document_id", "document_version_id"]
    assert [
        element.target_fullname
        for element in constraints[
            "fk_document_version_decision_records_exact_version"
        ].elements
    ] == [
        "document_versions.org_id",
        "document_versions.workspace_id",
        "document_versions.document_id",
        "document_versions.id",
    ]


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
        name=f"{document_hash}.pdf",
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
        source_filename=f"{version_hash[:12]}.pdf",
        source_mime="application/pdf",
        source_size_bytes=10,
        source_storage_key=f"versions/{version_hash}/source",
        source_page_count=1,
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        state=state,
        created_by=user.id,
    )
    session.add(version)
    await session.flush()
    return document, version


async def test_version_content_identity_is_unique_per_logical_document(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document, first = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="logical-version-identity",
        version_hash="same-version-content",
    )
    session.add(
        DocumentVersion(
            org_id=organization.id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=2,
            version_label="Rev 2",
            version_key="rev 2",
            content_hash=first.content_hash,
            source_filename="r2.pdf",
            source_mime="application/pdf",
            source_size_bytes=12,
            source_storage_key="versions/r2/source",
            source_page_count=1,
            parser_profile_version="docling/v1",
            ocr_profile_version="none/v1",
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
            index_profile_version="hybrid/v1",
            created_by=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


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
        page_start=1,
        page_end=1,
        section_path=["Section"],
        content_hash="a" * 64,
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
    )
    block = DocumentBlock(
        org_id=organization.id,
        document_version_id=second.id,
        ordinal=0,
        text="block",
        page_number=1,
        locator_kind="page",
        locator_label="1",
        block_type="paragraph",
        section_path=["Section"],
        extraction_method="parser",
        ocr_profile_version="none/v1",
        content_hash="b" * 64,
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
        page_start=1,
        page_end=1,
        section_path=["Section"],
        content_hash="c" * 64,
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
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
            page_start=1,
            page_end=1,
            section_path=["Section"],
            content_hash="d" * 64,
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
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
        page_start=1,
        page_end=1,
        section_path=["Summary"],
        content_hash="e" * 64,
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
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


async def test_non_ocr_profile_sentinel_is_valid_for_native_document(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="native-docx",
        version_hash="native-docx-version",
    )
    await session.commit()
    assert version.ocr_profile_version == "none/v1"


@pytest.mark.parametrize(
    ("field", "legacy_value"),
    [
        ("parser_profile_version", "legacy/parser-v1"),
        ("ocr_profile_version", "legacy/ocr-unknown-v1"),
        ("chunking_profile_version", "legacy/chunking-v1"),
        ("embedding_profile_version", "legacy/embedding-v1"),
        ("index_profile_version", "legacy/index-v1"),
        ("provenance_state", "legacy_pending"),
    ],
)
async def test_nonlegacy_version_rejects_every_legacy_only_provenance_value(
    session: AsyncSession,
    field: str,
    legacy_value: str,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"nonlegacy-{field}",
        version_hash=f"nonlegacy-{field}-version",
    )
    setattr(version, field, legacy_value)

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.parametrize("version_kind", ["legacy", "authority"])
@pytest.mark.parametrize(
    "profile_field",
    [
        "parser_profile_version",
        "ocr_profile_version",
        "chunking_profile_version",
        "embedding_profile_version",
        "index_profile_version",
    ],
)
async def test_every_version_profile_is_required_in_postgresql(
    session: AsyncSession,
    version_kind: str,
    profile_field: str,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        name=f"{version_kind} profile contract",
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    values: dict[str, object] = {
        "org_id": organization.id,
        "workspace_id": workspace.id,
        "document_id": document.id,
        "sequence": 1,
        "content_hash": "9" * 64,
        "source_filename": "source.pdf",
        "source_mime": "application/pdf",
        "source_size_bytes": 10,
        "source_storage_key": "versions/source.pdf",
        "source_page_count": 1,
        "created_by": user.id,
    }
    if version_kind == "legacy":
        values.update(
            {
                "version_label": "Legacy 1",
                "version_key": "legacy 1",
                "parser_profile_version": "legacy/parser-v1",
                "ocr_profile_version": "legacy/ocr-unknown-v1",
                "chunking_profile_version": "legacy/chunking-v1",
                "embedding_profile_version": "legacy/embedding-v1",
                "index_profile_version": "legacy/index-v1",
                "state": "approved",
                "provenance_state": "legacy_pending",
            }
        )
    else:
        values.update(
            {
                "version_label": "Rev 1",
                "version_key": "rev 1",
                "parser_profile_version": "docling/v1",
                "ocr_profile_version": "none/v1",
                "chunking_profile_version": "semantic/v1",
                "embedding_profile_version": "bge-m3/v1",
                "index_profile_version": "hybrid/v1",
                "state": "draft",
                "provenance_state": "none",
            }
        )
    values[profile_field] = None

    with pytest.raises(IntegrityError):
        await session.execute(insert(DocumentVersion).values(**values))
        await session.commit()


@pytest.mark.parametrize(
    ("legacy_status", "state", "provenance_state"),
    [
        ("indexed", "approved", "legacy_pending"),
        ("failed", "failed", "none"),
        ("queued", "processing", "none"),
        ("processing", "processing", "none"),
    ],
)
async def test_exact_legacy_backfill_mappings_retain_source_identity(
    session: AsyncSession,
    legacy_status: str,
    state: str,
    provenance_state: str,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        name="Legacy report.pdf",
        filename="report.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="7" * 64,
        status=legacy_status,
        storage_key="legacy/report.pdf",
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        org_id=organization.id,
        workspace_id=workspace.id,
        document_id=document.id,
        sequence=1,
        version_label="Legacy 1",
        version_key="legacy 1",
        content_hash="7" * 64,
        source_filename=document.filename,
        source_mime=document.mime,
        source_size_bytes=document.size_bytes,
        source_storage_key=document.storage_key,
        source_page_count=None,
        parser_profile_version="legacy/parser-v1",
        ocr_profile_version="legacy/ocr-unknown-v1",
        chunking_profile_version="legacy/chunking-v1",
        embedding_profile_version="legacy/embedding-v1",
        index_profile_version="legacy/index-v1",
        state=state,
        provenance_state=provenance_state,
        created_by=user.id,
    )
    session.add(version)
    await session.commit()
    assert version.source_storage_key == "legacy/report.pdf"
    assert version.source_filename == "report.pdf"

    document.filename = None
    document.mime = None
    document.size_bytes = None
    document.content_hash = None
    document.storage_key = None
    await session.commit()
    source = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == version.id)
        )
    ).scalar_one()
    assert (
        source.source_filename,
        source.source_mime,
        source.source_size_bytes,
        source.content_hash,
        source.source_storage_key,
    ) == (
        "report.pdf",
        "application/pdf",
        10,
        "7" * 64,
        "legacy/report.pdf",
    )


async def test_legacy_version_never_allows_missing_source_identity(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        name="Legacy missing source",
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentVersion(
            org_id=organization.id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=1,
            version_label="Legacy 1",
            version_key="legacy 1",
            content_hash="7" * 64,
            state="approved",
            provenance_state="legacy_pending",
            created_by=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_incomplete_authority_version_is_rejected(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    document = Document(
        org_id=organization.id,
        workspace_id=workspace.id,
        name="Incomplete authority",
        filename="incomplete.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="legacy-mirror-incomplete",
        storage_key="legacy/incomplete.pdf",
        created_by=user.id,
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentVersion(
            org_id=organization.id,
            workspace_id=workspace.id,
            document_id=document.id,
            sequence=1,
            version_label="Rev 1",
            version_key="rev 1",
            content_hash="8" * 64,
            created_by=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_incomplete_block_and_chunk_provenance_is_rejected(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="incomplete-block",
        version_hash="incomplete-block-version",
    )
    session.add(
        DocumentBlock(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="missing provenance",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="incomplete-chunk",
        version_hash="incomplete-chunk-version",
    )
    session.add(
        DocumentChunk(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="missing provenance",
            token_count=2,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_scanner_cursor_survives_version_deletion_as_resume_watermark(
    session: AsyncSession,
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash="scanner-watermark",
        version_hash="scanner-watermark-version",
    )
    checkpoint = LegacyRebuildScanCheckpoint(
        org_id=organization.id,
        workspace_id=workspace.id,
        cursor_document_version_id=version.id,
        pass_number=1,
        scanned_count=1,
    )
    session.add(checkpoint)
    await session.commit()
    cursor = version.id

    await session.delete(version)
    await session.commit()
    await session.refresh(checkpoint)
    assert checkpoint.cursor_document_version_id == cursor


@pytest.mark.parametrize("authority_kind", ["block", "chunk", "evidence"])
@pytest.mark.parametrize(
    "invalid_section",
    [[123], [{"heading": "object"}], [""], ["x" * 201], ["Valid", 3]],
)
async def test_section_jsonb_elements_are_bounded_strings_in_postgresql(
    session: AsyncSession,
    authority_kind: str,
    invalid_section: list[object],
) -> None:
    organization, workspace, user = await seed_scope(session)
    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"section-{authority_kind}-{uuid4()}",
        version_hash=f"section-version-{uuid4()}",
    )
    if authority_kind == "block":
        statement = insert(DocumentBlock).values(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="block",
            page_number=1,
            locator_kind="page",
            locator_label="1",
            block_type="paragraph",
            section_path=invalid_section,
            extraction_method="parser",
            ocr_profile_version="none/v1",
            content_hash="a" * 64,
        )
    elif authority_kind == "chunk":
        statement = insert(DocumentChunk).values(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="chunk",
            token_count=1,
            page_start=1,
            page_end=1,
            section_path=invalid_section,
            content_hash="b" * 64,
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
        )
    else:
        chunk = DocumentChunk(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="chunk",
            token_count=1,
            page_start=1,
            page_end=1,
            section_path=["Valid"],
            content_hash="b" * 64,
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
        )
        session.add(chunk)
        await session.flush()
        statement = insert(DocumentEvidenceSpan).values(
            org_id=organization.id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            page_number=1,
            locator_kind="page",
            locator_label="1",
            section_path=invalid_section,
            content_hash="c" * 64,
            ordinal=0,
            token_count=1,
            artifact_byte_start=0,
            artifact_byte_end=5,
        )
    with pytest.raises(DBAPIError):
        await session.execute(statement)
        await session.commit()


@pytest.mark.parametrize("authority_kind", ["version", "block", "chunk", "evidence"])
async def test_authority_content_hashes_require_lowercase_sha256(
    session: AsyncSession,
    authority_kind: str,
) -> None:
    organization, workspace, user = await seed_scope(session)
    if authority_kind == "version":
        with pytest.raises(IntegrityError):
            await seed_document_version(
                session,
                organization=organization,
                workspace=workspace,
                user=user,
                document_hash="nonhex-version",
                version_hash="g" * 64,
            )
        return

    _, version = await seed_document_version(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        document_hash=f"nonhex-{authority_kind}",
        version_hash=f"valid-{authority_kind}",
    )
    if authority_kind == "block":
        row = DocumentBlock(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="block",
            page_number=1,
            locator_kind="page",
            locator_label="1",
            block_type="paragraph",
            section_path=["Valid"],
            extraction_method="parser",
            ocr_profile_version="none/v1",
            content_hash="g" * 64,
        )
    else:
        chunk = DocumentChunk(
            org_id=organization.id,
            document_version_id=version.id,
            ordinal=0,
            text="chunk",
            token_count=1,
            page_start=1,
            page_end=1,
            section_path=["Valid"],
            content_hash="g" * 64 if authority_kind == "chunk" else "d" * 64,
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
        )
        session.add(chunk)
        if authority_kind == "chunk":
            with pytest.raises(IntegrityError):
                await session.flush()
            return
        await session.flush()
        row = DocumentEvidenceSpan(
            org_id=organization.id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            page_number=1,
            locator_kind="page",
            locator_label="1",
            section_path=["Valid"],
            content_hash="g" * 64,
            ordinal=0,
            token_count=1,
            artifact_byte_start=0,
            artifact_byte_end=5,
        )
    session.add(row)
    with pytest.raises(IntegrityError):
        await session.commit()


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
