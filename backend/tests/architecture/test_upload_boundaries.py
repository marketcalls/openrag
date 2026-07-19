import ast
from pathlib import Path


def test_uploadfile_reads_are_always_explicitly_bounded() -> None:
    source_root = Path(__file__).parents[2] / "src" / "openrag"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "read":
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in {"file", "upload"}:
                if not node.args and not node.keywords:
                    violations.append(f"{path.relative_to(source_root)}:{node.lineno}")
    assert violations == []


def test_public_upload_route_uses_validated_quarantine_boundary() -> None:
    route = (
        Path(__file__).parents[2]
        / "src"
        / "openrag"
        / "api"
        / "routes"
        / "documents.py"
    ).read_text(encoding="utf-8")

    assert "quarantine_upload(file, settings)" in route
    assert "create_from_quarantined_upload" in route
    assert "await file.read()" not in route
