from openrag.api.app import create_app


def test_memory_routes_are_registered() -> None:
    app = create_app()
    paths = app.openapi()["paths"]

    assert "post" in paths["/api/v1/workspaces/{workspace_id}/memories"]
    assert "get" in paths["/api/v1/workspaces/{workspace_id}/memories"]
    assert "get" in paths["/api/v1/workspaces/{workspace_id}/memories/export"]
    assert "patch" in paths["/api/v1/workspaces/{workspace_id}/memories/preferences"]
    assert "post" in paths[
        "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/forget"
    ]
