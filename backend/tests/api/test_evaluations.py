from openrag.api.app import create_app


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
