from types import SimpleNamespace
from uuid import UUID

import pytest

from openrag.modules.documents import ingest
from openrag.worker import tasks
from openrag.worker.celery_app import celery_app
from openrag.worker.tasks import (
    IngestTask,
    build_ingest_chain,
    build_legacy_ingest_chain,
    enqueue_ingest,
    parse_task,
    select_queue,
)


def test_celery_config() -> None:
    assert celery_app.conf.task_acks_late is True
    assert "openrag.worker.tasks" in celery_app.conf.include
    assert {queue.name for queue in celery_app.conf.task_queues} == {
        "default",
        "interactive",
    }


def test_queue_selection_by_size() -> None:
    assert select_queue(5 * 1024 * 1024) == "interactive"
    assert select_queue(10 * 1024 * 1024) == "default"
    assert select_queue(50 * 1024 * 1024) == "default"


def test_ingest_chain_structure() -> None:
    signature = build_ingest_chain("doc-id-123", "interactive", 7)

    assert [task.task for task in signature.tasks] == [
        "documents.parse",
        "documents.chunk",
        "documents.embed_upsert",
    ]
    assert all(task.options.get("queue") == "interactive" for task in signature.tasks)
    assert all(task.args == ("doc-id-123", 7) for task in signature.tasks)


def test_legacy_ingest_chain_structure() -> None:
    signature = build_legacy_ingest_chain("doc-id-123", "interactive")

    assert [task.task for task in signature.tasks] == [
        "documents.parse",
        "documents.chunk",
        "documents.embed_upsert",
    ]
    assert all(
        task.options.get("queue") == "interactive"
        for task in signature.tasks
    )
    assert all(task.args == ("doc-id-123",) for task in signature.tasks)


def test_revision_protocol_dispatch_is_fail_closed_until_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            interactive_upload_mb=10,
            ingest_revision_protocol_v2_enabled=False,
        ),
    )
    with pytest.raises(RuntimeError, match="worker revision protocol v2"):
        enqueue_ingest(UUID("12345678-1234-5678-1234-567812345678"), 10, 2)


def test_revision_one_dispatch_uses_legacy_envelope_before_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[bool] = []

    class Signature:
        def apply_async(self) -> None:
            applied.append(True)

    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            interactive_upload_mb=10,
            ingest_revision_protocol_v2_enabled=False,
        ),
    )
    monkeypatch.setattr(
        tasks, "build_legacy_ingest_chain", lambda *_args: Signature()
    )
    enqueue_ingest(UUID("12345678-1234-5678-1234-567812345678"), 10, 1)
    assert applied == [True]


def test_revision_protocol_dispatch_is_enabled_after_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[bool] = []

    class Signature:
        def apply_async(self) -> None:
            applied.append(True)

    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            interactive_upload_mb=10,
            ingest_revision_protocol_v2_enabled=True,
        ),
    )
    monkeypatch.setattr(tasks, "build_ingest_chain", lambda *_args: Signature())
    enqueue_ingest(UUID("12345678-1234-5678-1234-567812345678"), 10, 1)
    assert applied == [True]


def test_legacy_one_argument_task_envelope_defaults_to_revision_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = UUID("12345678-1234-5678-1234-567812345678")
    calls: list[tuple[UUID, int]] = []

    async def run_parse(doc_id: UUID, revision: int) -> None:
        calls.append((doc_id, revision))

    monkeypatch.setattr(ingest, "run_parse", run_parse)

    assert parse_task.run(str(document_id)) == str(document_id)
    assert calls == [(document_id, 1)]


def test_legacy_failure_hook_defaults_to_revision_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = UUID("12345678-1234-5678-1234-567812345678")
    calls: list[tuple[UUID, int, str]] = []

    async def mark_failed(doc_id: UUID, revision: int, reason: str) -> None:
        calls.append((doc_id, revision, reason))

    monkeypatch.setattr(ingest, "mark_failed", mark_failed)
    IngestTask().on_failure(
        RuntimeError("legacy failure"),
        "task-id",
        (str(document_id),),
        {},
        None,
    )
    assert calls == [(document_id, 1, "legacy failure")]
