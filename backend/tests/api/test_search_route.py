import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS
from tests.api.test_documents_routes import auth, make_workspace
from tests.modules.retrieval.test_retrieve import upsert_texts


async def test_search_empty_workspace_is_no_answer(
    client: httpx.AsyncClient,
    seeded_user: User,
    qdrant_collection: None,
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        json={"query": "anything"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"no_answer": True, "chunks": []}


async def test_search_returns_seeded_chunk(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    headers = await auth(client, seeded_user.email)
    workspace_id = await make_workspace(client, headers)
    workspace = (await session.execute(select(Workspace))).scalar_one()
    workspace.min_score = 0.0
    # This fixture writes directly to the legacy collection. New workspaces are
    # authority-mode by default and correctly fail closed without a deployment.
    workspace.document_authority_enabled = False
    await session.commit()
    context = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        authorization=AuthorizationSnapshot(
            user_id=seeded_user.id,
            org_id=seeded_user.org_id,
            is_platform_superadmin=False,
            org_permissions=ALL_PERMISSIONS,
            workspace_permissions={},
            workspace_ids=frozenset({workspace.id}),
        ),
    )
    await upsert_texts(
        session,
        context,
        workspace,
        ["invoice 0231 covers the plutonium delivery"],
    )

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        json={"query": "invoice 0231"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["no_answer"] is False
    assert "invoice 0231" in body["chunks"][0]["text"]
    assert {"document_id", "page", "chunk_index", "text", "score"} <= set(
        body["chunks"][0]
    )


async def test_search_requires_auth(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000000/search",
        json={"query": "x"},
    )

    assert response.status_code == 401
