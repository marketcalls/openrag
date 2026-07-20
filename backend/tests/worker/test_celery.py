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
        "events",
        "evaluations",
        "ingestion",
        "interactive",
        "models",
        "runs",
        "summaries",
    }
    dispatch_schedule = celery_app.conf.beat_schedule["dispatch-outbox"]
    assert dispatch_schedule["task"] == "events.dispatch_outbox"
    assert dispatch_schedule["options"]["queue"] == "events"
    assert dispatch_schedule["options"]["expires"] <= 10
    starts_schedule = celery_app.conf.beat_schedule["consume-document-starts"]
    assert starts_schedule["task"] == "events.consume_document_starts"
    assert starts_schedule["options"]["queue"] == "events"
    assert starts_schedule["options"]["expires"] <= 10
    lifecycle_schedule = celery_app.conf.beat_schedule["consume-document-lifecycle"]
    assert lifecycle_schedule["task"] == "events.consume_document_lifecycle"
    assert lifecycle_schedule["options"]["queue"] == "events"
    assert lifecycle_schedule["options"]["expires"] <= 10
    run_commands_schedule = celery_app.conf.beat_schedule["consume-run-commands"]
    assert run_commands_schedule["task"] == "events.consume_run_commands"
    assert run_commands_schedule["options"]["queue"] == "events"
    assert run_commands_schedule["options"]["expires"] <= 10
    run_schedule = celery_app.conf.beat_schedule["execute-agent-run"]
    assert run_schedule["task"] == "runs.execute_next"
    assert run_schedule["options"]["queue"] == "runs"
    assert run_schedule["options"]["expires"] <= 2
    summary_schedule = celery_app.conf.beat_schedule["refresh-conversation-summary"]
    assert summary_schedule["task"] == "summaries.refresh_next"
    assert summary_schedule["options"]["queue"] == "summaries"
    assert summary_schedule["options"]["expires"] <= 5
    stage_schedule = celery_app.conf.beat_schedule["run-durable-document-stage"]
    assert stage_schedule["task"] == "documents.run_durable_stage"
    assert stage_schedule["options"]["queue"] == "ingestion"
    assert stage_schedule["options"]["expires"] <= 10
    eligibility_schedule = celery_app.conf.beat_schedule["sync-vector-eligibility"]
    assert eligibility_schedule["task"] == "documents.sync_vector_eligibility"
    assert eligibility_schedule["options"]["queue"] == "ingestion"
    assert eligibility_schedule["options"]["expires"] <= 10
    parse_limits = celery_app.conf.task_annotations["documents.parse"]
    assert parse_limits["soft_time_limit"] == 305
    assert parse_limits["time_limit"] == 330
    stage_limits = celery_app.conf.task_annotations["documents.run_durable_stage"]
    assert stage_limits["soft_time_limit"] == 900
    assert stage_limits["time_limit"] == 930
    assert celery_app.conf.worker_max_tasks_per_child == 25
    assert celery_app.conf.worker_max_memory_per_child == 3 * 1024 * 1024


def test_event_tasks_are_isolated_to_the_events_queue() -> None:
    route = celery_app.conf.task_routes["events.*"]

    assert route == {"queue": "events"}
    assert "events.dispatch_outbox" in celery_app.tasks
    assert "events.consume_document_starts" in celery_app.tasks
    assert "events.consume_document_lifecycle" in celery_app.tasks
    assert "events.consume_run_commands" in celery_app.tasks
    assert "documents.run_durable_stage" in celery_app.tasks
    assert "documents.sync_vector_eligibility" in celery_app.tasks
    assert celery_app.conf.task_routes["runs.*"] == {"queue": "runs"}
    assert celery_app.conf.task_routes["summaries.*"] == {"queue": "summaries"}
    assert "runs.execute_next" in celery_app.tasks
    assert "summaries.refresh_next" in celery_app.tasks
    assert celery_app.conf.task_routes["evaluations.*"] == {"queue": "evaluations"}
    assert "evaluations.execute_next" in celery_app.tasks
    evaluation_schedule = celery_app.conf.beat_schedule["execute-rag-evaluation"]
    assert evaluation_schedule["task"] == "evaluations.execute_next"
    assert evaluation_schedule["options"]["queue"] == "evaluations"
    automation_schedule = celery_app.conf.beat_schedule["schedule-rag-evaluations"]
    assert automation_schedule["task"] == "evaluations.schedule_due"
    assert automation_schedule["options"]["queue"] == "evaluations"
    assert "evaluations.schedule_due" in celery_app.tasks
    assert celery_app.conf.task_routes["models.*"] == {"queue": "models"}
    assert "models.execute_probe" in celery_app.tasks
    probe_schedule = celery_app.conf.beat_schedule["execute-model-probe"]
    assert probe_schedule["task"] == "models.execute_probe"
    assert probe_schedule["options"]["queue"] == "models"
    assert probe_schedule["options"]["expires"] <= 3


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
    assert all(task.options.get("queue") == "interactive" for task in signature.tasks)
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
    monkeypatch.setattr(tasks, "build_legacy_ingest_chain", lambda *_args: Signature())
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
