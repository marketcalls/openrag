from pathlib import Path

import pytest

from openrag.core.errors import NotFoundError
from openrag.core.storage import ObjectStorage


async def test_put_get_delete_roundtrip(storage: ObjectStorage) -> None:
    key = "org/workspace/document/file.txt"
    await storage.put(key, b"hello openrag", content_type="text/plain")

    assert await storage.get(key) == b"hello openrag"

    await storage.delete(key)
    with pytest.raises(NotFoundError):
        await storage.get(key)


async def test_put_file_streams_path_to_object_storage(
    storage: ObjectStorage,
    tmp_path: Path,
) -> None:
    source = tmp_path / "validated.txt"
    source.write_bytes(b"validated upload")

    await storage.put_file(
        "org/workspace/document/validated.txt",
        source,
        content_type="text/plain",
    )

    assert (
        await storage.get("org/workspace/document/validated.txt")
        == b"validated upload"
    )


async def test_delete_missing_is_idempotent(storage: ObjectStorage) -> None:
    await storage.delete("does/not/exist")


async def test_ensure_bucket_is_idempotent(storage: ObjectStorage) -> None:
    await storage.ensure_bucket()
    await storage.ensure_bucket()
