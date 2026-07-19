import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import (
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentChunkBlock,
    DocumentEvidenceSpan,
    DocumentVersion,
    IngestJob,
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
