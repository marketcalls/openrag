import asyncio
from uuid import UUID, uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.auth.models import User
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from openrag.modules.retrieval.service import delete_document_points, retrieve
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Organization, Workspace, WorkspaceMember
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS


async def seed_workspace(
    session: AsyncSession,
    org_name: str,
    *,
    role: str = "user",
    member: bool = True,
    min_score: float = 0.0,
) -> tuple[TenantContext, Workspace]:
    organization = Organization(name=org_name)
    session.add(organization)
    await session.flush()
    workspace = Workspace(
        org_id=organization.id,
        name="Workspace",
        min_score=min_score,
    )
    user = User(
        org_id=organization.id,
        email=f"user@{org_name}.com",
        password_hash="x",  # noqa: S106 - inert persisted test value
    )
    session.add_all([workspace, user])
    await session.flush()
    if member:
        session.add(
            WorkspaceMember(
                org_id=organization.id,
                workspace_id=workspace.id,
                user_id=user.id,
            )
        )
    await session.commit()
    permissions = (
        ALL_PERMISSIONS
        if role == "admin"
        else frozenset({"document.read", "document.upload"})
    )
    context = TenantContext(
        user_id=user.id,
        org_id=organization.id,
        authorization=AuthorizationSnapshot(
            user_id=user.id,
            org_id=organization.id,
            is_platform_superadmin=False,
            org_permissions=permissions,
            workspace_permissions={},
            workspace_ids=(
                frozenset({workspace.id}) if member else frozenset()
            ),
        ),
    )
    return context, workspace


async def upsert_texts(
    context: TenantContext,
    workspace: Workspace,
    texts: list[str],
) -> str:
    document_id = str(uuid4())
    dense_vectors = await get_dense_embedder().embed(texts)
    sparse_vectors = await asyncio.to_thread(embed_sparse, texts)
    points = [
        models.PointStruct(
            id=str(uuid4()),
            vector={"dense": dense, "sparse": sparse},
            payload={
                "tenant_id": str(context.org_id),
                "workspace_id": str(workspace.id),
                "document_id": document_id,
                "page": index + 1,
                "chunk_index": index,
                "text": text,
                "doc_type": "text/plain",
                "date": "2026-07-18",
                "acl_groups": [],
            },
        )
        for index, (text, dense, sparse) in enumerate(
            zip(texts, dense_vectors, sparse_vectors, strict=True)
        )
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
    return document_id


async def test_retrieve_returns_matching_chunk(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-a")
    await upsert_texts(
        context,
        workspace,
        [
            "the flux capacitor requires 1.21 gigawatts",
            "unrelated kumquat farming notes",
        ],
    )

    result = await retrieve(
        session,
        context,
        workspace.id,
        "flux capacitor gigawatts",
        top_k=2,
    )

    assert not result.no_answer
    assert result.chunks[0].text.startswith("the flux capacitor")
    assert result.chunks[0].page == 1
    assert result.chunks[0].chunk_index == 0


async def test_min_score_triggers_no_answer_with_nearest_chunk(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "retrieval-b",
        min_score=0.99,
    )
    await upsert_texts(context, workspace, ["vaguely related invoice text"])

    result = await retrieve(
        session,
        context,
        workspace.id,
        "completely different query terms",
    )

    assert result.no_answer
    assert result.chunks


async def test_empty_workspace_is_no_answer(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-c")

    result = await retrieve(session, context, workspace.id, "anything")

    assert result.no_answer
    assert result.chunks == []


async def test_non_member_is_denied(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "retrieval-d",
        member=False,
    )

    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, context, workspace.id, "anything")


async def test_admin_without_membership_is_allowed(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "retrieval-e",
        role="admin",
        member=False,
    )

    result = await retrieve(session, context, workspace.id, "anything")

    assert result.chunks == []


async def test_delete_document_points(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-f")
    document_id = await upsert_texts(context, workspace, ["target text to delete"])

    await delete_document_points(context.org_id, UUID(document_id))
    result = await retrieve(
        session,
        context,
        workspace.id,
        "target text to delete",
    )

    assert result.chunks == []
