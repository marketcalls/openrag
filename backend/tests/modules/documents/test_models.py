import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.documents.models import Document, IngestJob
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
        role="admin",
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
