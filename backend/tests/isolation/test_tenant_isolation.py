"""Adversarial tenant-leak tests that must run on every change."""

from dataclasses import replace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.documents.ingest import run_delete
from openrag.modules.documents.models import Document
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import get_dense_embedder
from openrag.modules.retrieval.service import retrieve
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace

TwoOrgs = dict[str, tuple[TenantContext, Workspace, Document]]


async def test_org_a_never_sees_org_b_chunks(
    session: AsyncSession,
    two_orgs: TwoOrgs,
) -> None:
    context_a, workspace_a, document_a = two_orgs["a"]
    _, _, document_b = two_orgs["b"]

    result = await retrieve(
        session,
        context_a,
        workspace_a.id,
        "org bravo secret: the vault code is 9962",
        top_k=10,
    )

    returned_documents = {chunk.document_id for chunk in result.chunks}
    assert document_b.id not in returned_documents
    assert all(item == document_a.id for item in returned_documents)
    assert all("9962" not in chunk.text for chunk in result.chunks)


async def test_cross_org_workspace_retrieval_is_denied(
    session: AsyncSession,
    two_orgs: TwoOrgs,
) -> None:
    context_a, _, _ = two_orgs["a"]
    _, workspace_b, _ = two_orgs["b"]

    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, context_a, workspace_b.id, "anything")


async def test_same_org_non_member_is_denied(
    session: AsyncSession,
    two_orgs: TwoOrgs,
) -> None:
    context_a, workspace_a, _ = two_orgs["a"]
    stranger = replace(context_a, workspace_ids=frozenset())

    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, stranger, workspace_a.id, "anything")


async def test_deleted_document_is_unretrievable(
    session: AsyncSession,
    two_orgs: TwoOrgs,
) -> None:
    context_a, workspace_a, document_a = two_orgs["a"]
    before = await retrieve(session, context_a, workspace_a.id, "vault code 7431")
    assert any(
        chunk.document_id == document_a.id for chunk in before.chunks
    )

    await run_delete(document_a.id, context_a.user_id)
    after = await retrieve(session, context_a, workspace_a.id, "vault code 7431")

    assert all(
        chunk.document_id != document_a.id for chunk in after.chunks
    )
    assert all("7431" not in chunk.text for chunk in after.chunks)


async def test_canary_unfiltered_query_sees_both_organizations(
    two_orgs: TwoOrgs,
) -> None:
    lure = (await get_dense_embedder().embed(["secret: the vault code is"]))[0]

    raw = await get_qdrant().query_points(
        COLLECTION,
        query=lure,
        using="dense",
        limit=10,
        with_payload=True,
    )

    tenants = {
        str((point.payload or {})["tenant_id"]) for point in raw.points
    }
    assert len(tenants) == 2
