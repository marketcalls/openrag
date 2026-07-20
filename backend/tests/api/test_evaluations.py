from typing import Any
from uuid import uuid4

import httpx
import pytest

from openrag.api.app import create_app
from openrag.modules.auth.models import User


def test_evaluation_api_exposes_dataset_and_run_workflows() -> None:
    paths = create_app().openapi()["paths"]

    assert set(paths["/api/v1/admin/evaluations/datasets"]) >= {"get", "post"}
    assert set(paths["/api/v1/admin/evaluations/datasets/{dataset_id}/versions"]) >= {
        "get",
        "post",
    }
    assert "get" in paths["/api/v1/admin/evaluations/versions/{version_id}"]
    assert set(paths["/api/v1/admin/evaluations/runs"]) >= {"get", "post"}
    assert "get" in paths["/api/v1/admin/evaluations/runs/{run_id}"]
    assert set(paths["/api/v1/admin/evaluations/policies"]) >= {"get", "put"}


async def _auth(client: httpx.AsyncClient, user: User) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "pw123456"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/admin/evaluations/datasets", None),
        (
            "POST",
            "/api/v1/admin/evaluations/datasets",
            {"workspace_id": str(uuid4()), "name": "Denied dataset"},
        ),
        ("GET", f"/api/v1/admin/evaluations/datasets/{uuid4()}/versions", None),
        (
            "POST",
            f"/api/v1/admin/evaluations/datasets/{uuid4()}/versions",
            {"cases": [{"question": "No evidence?", "should_refuse": True}]},
        ),
        ("GET", f"/api/v1/admin/evaluations/versions/{uuid4()}", None),
        ("GET", "/api/v1/admin/evaluations/runs", None),
        ("GET", "/api/v1/admin/evaluations/policies", None),
        (
            "PUT",
            "/api/v1/admin/evaluations/policies",
            {
                "dataset_id": str(uuid4()),
                "model_id": str(uuid4()),
                "interval_hours": 24,
                "max_cases": 10,
                "max_tokens": 1000,
                "max_cost_microusd": 10_000,
            },
        ),
        (
            "POST",
            "/api/v1/admin/evaluations/runs",
            {
                "dataset_version_id": str(uuid4()),
                "model_id": str(uuid4()),
                "max_cases": 1,
                "max_tokens": 100,
                "max_cost_microusd": 10_000,
            },
        ),
        ("GET", f"/api/v1/admin/evaluations/runs/{uuid4()}", None),
    ],
)
async def test_every_evaluation_route_is_platform_superadmin_only(
    client: httpx.AsyncClient,
    seeded_user: User,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = await client.request(
        method,
        path,
        json=body,
        headers=await _auth(client, seeded_user),
    )

    assert response.status_code == 403
