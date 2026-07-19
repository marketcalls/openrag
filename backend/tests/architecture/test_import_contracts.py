import ast
from pathlib import Path


def test_outbox_rows_are_created_only_through_registered_factory() -> None:
    source_root = Path(__file__).parents[2] / "src" / "openrag"
    violations: list[str] = []
    allowed = source_root / "modules" / "events" / "outbox.py"

    for path in source_root.rglob("*.py"):
        if path == allowed:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "OutboxEvent")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "OutboxEvent")
            ):
                violations.append(f"{path.relative_to(source_root)}:{node.lineno}")

    assert violations == []
