from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.operations.models import ErrorIssue, ErrorOccurrence
from openrag.modules.tenancy.models import Workspace


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw123456"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_non_platform_admin_cannot_read_rag_operations(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await auth(client, seeded_user.email)
    now = datetime.now(UTC)

    response = await client.get(
        "/api/v1/admin/rag-operations/overview",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers=headers,
    )

    assert response.status_code == 403


async def test_superadmin_reads_empty_bounded_overview(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)
    now = datetime.now(UTC)

    response = await client.get(
        "/api/v1/admin/rag-operations/overview",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "query_count": 0,
        "grounded_count": 0,
        "no_answer_count": 0,
        "failed_count": 0,
        "cancelled_count": 0,
        "grounded_rate": 0.0,
        "no_answer_rate": 0.0,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "p99_latency_ms": None,
        "average_ttft_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "estimated_cost_microusd": 0,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/rag-operations/quality",
        "/api/v1/admin/rag-operations/series",
        "/api/v1/admin/rag-operations/runs",
        "/api/v1/admin/rag-operations/runs/00000000-0000-0000-0000-000000000001",
        "/api/v1/admin/rag-operations/errors",
        "/api/v1/admin/rag-operations/errors/00000000-0000-0000-0000-000000000001",
    ],
)
async def test_all_operations_routes_are_platform_superadmin_only(
    client: httpx.AsyncClient,
    seeded_user: User,
    path: str,
) -> None:
    headers = await auth(client, seeded_user.email)
    now = datetime.now(UTC)

    response = await client.get(
        path,
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers=headers,
    )

    assert response.status_code == 403


async def test_superadmin_reads_empty_content_free_quality_overview(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await auth(client, seeded_superadmin.email)
    now = datetime.now(UTC)

    response = await client.get(
        "/api/v1/admin/rag-operations/quality",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "scheduled_count": 0,
        "completed_count": 0,
        "passed_count": 0,
        "rejected_count": 0,
        "pending_count": 0,
        "skipped_count": 0,
        "worker_failed_count": 0,
        "completion_rate": 0.0,
        "pass_rate": 0.0,
        "average_grounding_score": None,
        "average_completeness_score": None,
    }


async def test_superadmin_error_scope_does_not_mix_tenant_counts_or_occurrences(
    client: httpx.AsyncClient,
    session: AsyncSession,
    seeded_user: User,
    seeded_superadmin: User,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    selected_workspace = Workspace(org_id=seeded_user.org_id, name="Selected tenant")
    foreign_workspace = Workspace(org_id=seeded_superadmin.org_id, name="Foreign tenant")
    session.add_all([selected_workspace, foreign_workspace])
    await session.flush()
    issue = ErrorIssue(
        fingerprint="a" * 64,
        category="retrieval",
        code="retrieval.timeout",
        service="api",
        environment="test",
        exception_type="TimeoutError",
        occurrence_count=2,
        first_seen_at=now - timedelta(minutes=5),
        last_seen_at=now - timedelta(minutes=1),
    )
    session.add(issue)
    await session.flush()
    session.add_all(
        [
            ErrorOccurrence(
                issue_id=issue.id,
                org_id=seeded_user.org_id,
                workspace_id=selected_workspace.id,
                code=issue.code,
                exception_type=issue.exception_type,
                occurred_at=now - timedelta(minutes=5),
            ),
            ErrorOccurrence(
                issue_id=issue.id,
                org_id=seeded_superadmin.org_id,
                workspace_id=foreign_workspace.id,
                code=issue.code,
                exception_type=issue.exception_type,
                occurred_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await session.commit()
    headers = await auth(client, seeded_superadmin.email)
    params = {
        "from": (now.replace(tzinfo=UTC) - timedelta(hours=1)).isoformat(),
        "to": (now.replace(tzinfo=UTC) + timedelta(minutes=1)).isoformat(),
        "org_id": str(seeded_user.org_id),
        "workspace_id": str(selected_workspace.id),
    }

    listing = await client.get(
        "/api/v1/admin/rag-operations/errors",
        params=params,
        headers=headers,
    )
    detail = await client.get(
        f"/api/v1/admin/rag-operations/errors/{issue.id}",
        params=params,
        headers=headers,
    )

    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["occurrence_count"] == 1
    assert detail.status_code == 200, detail.text
    assert detail.json()["issue"]["occurrence_count"] == 1
    assert [item["org_id"] for item in detail.json()["occurrences"]] == [
        str(seeded_user.org_id)
    ]
