from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from openrag.tools.batch_ingest import discover_files, ingest_batch, validate_batch


def test_discovery_is_recursive_supported_deduplicated_and_deterministic(
    tmp_path: Path,
) -> None:
    first = tmp_path / "A.pdf"
    first.write_bytes(b"pdf")
    nested = tmp_path / "nested"
    nested.mkdir()
    second = nested / "b.txt"
    second.write_text("text")
    (nested / "ignored.exe").write_bytes(b"binary")

    result = discover_files((nested, first, first))

    assert result == (first, second)


def test_batch_capacity_is_configurable_but_never_below_100_mb(tmp_path: Path) -> None:
    path = tmp_path / "small.pdf"
    path.write_bytes(b"pdf")

    with pytest.raises(ValueError, match="batch_capacity_below_100_mb"):
        validate_batch((path,), max_total_bytes=99 * 1024 * 1024)
    assert validate_batch((path,), max_total_bytes=100 * 1024 * 1024) == 3


async def test_batch_upload_uses_public_api_and_returns_sanitized_results(
    tmp_path: Path,
) -> None:
    first = tmp_path / "one.pdf"
    second = tmp_path / "two.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    document_ids = iter((uuid4(), uuid4()))
    opaque_token = uuid4().hex
    seen_auth: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["Authorization"])
        return httpx.Response(201, json={"id": str(next(document_ids))})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await ingest_batch(
            (first, second),
            base_url="https://openrag.example",
            workspace_id=uuid4(),
            token=opaque_token,
            max_total_bytes=100 * 1024 * 1024,
            concurrency=2,
            client=client,
        )

    assert [item.status for item in results] == ["queued", "queued"]
    assert all(item.document_id for item in results)
    assert seen_auth == [f"Bearer {opaque_token}", f"Bearer {opaque_token}"]
    assert opaque_token not in repr(results)
