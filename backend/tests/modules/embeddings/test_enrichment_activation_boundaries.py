import ast
from pathlib import Path


def _called_names(function: ast.AsyncFunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_embedding_control_plane_never_runs_enrichment_backfill_inline() -> None:
    path = (
        Path(__file__).parents[3]
        / "src"
        / "openrag"
        / "modules"
        / "embeddings"
        / "service.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
    }

    for function_name in ("request_deployment", "activate_deployment"):
        assert "enqueue_enrichment_jobs" not in _called_names(
            functions[function_name]
        )
