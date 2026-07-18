import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.documents.ingest import (
    run_chunk,
    run_embed_upsert,
    run_parse,
)
from openrag.modules.documents.models import Document
from openrag.modules.documents.service import create_from_upload
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


async def ingest_text(
    session: AsyncSession,
    context: TenantContext,
    workspace: Workspace,
    filename: str,
    text: str,
) -> Document:
    document = await create_from_upload(
        session,
        context,
        workspace.id,
        filename=filename,
        mime="text/plain",
        data=text.encode(),
    )
    await run_parse(document.id)
    await run_chunk(document.id)
    await run_embed_upsert(document.id)
    await session.refresh(document)
    assert document.status == "indexed"
    return document


@pytest.fixture
async def two_orgs(
    session: AsyncSession,
    qdrant_collection: None,
) -> dict[str, tuple[TenantContext, Workspace, Document]]:
    context_a, workspace_a = await seed_workspace(session, "isolation-a")
    context_b, workspace_b = await seed_workspace(session, "isolation-b")
    document_a = await ingest_text(
        session,
        context_a,
        workspace_a,
        "a.txt",
        "org alpha secret: the vault code is 7431",
    )
    document_b = await ingest_text(
        session,
        context_b,
        workspace_b,
        "b.txt",
        "org bravo secret: the vault code is 9962",
    )
    return {
        "a": (context_a, workspace_a, document_a),
        "b": (context_b, workspace_b, document_b),
    }
