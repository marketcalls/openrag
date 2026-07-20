"""Thin synchronous Celery wrappers over asynchronous ingestion runners."""

import asyncio
from typing import Any
from uuid import UUID

from celery import Task, chain

from openrag.core.config import get_settings
from openrag.modules.documents import ingest
from openrag.modules.documents.pipeline import IngestFailure
from openrag.modules.documents.projection_runtime import (
    run_eligibility_projection_once,
)
from openrag.modules.documents.stage_runtime import run_durable_stage_once
from openrag.modules.embeddings.deployment_runtime import run_deployment_scan_once
from openrag.worker.celery_app import celery_app
from openrag.worker.evaluation_runtime import run_evaluation_once
from openrag.worker.event_runtime import (
    consume_document_lifecycle_once,
    consume_document_starts_once,
    consume_run_commands_once,
    dispatch_outbox_once,
    execute_run_once,
    execute_summary_once,
)

_MAX_RETRIES = 3


@celery_app.task(
    name="events.dispatch_outbox",
    ignore_result=True,
    soft_time_limit=20,
    time_limit=25,
)
def dispatch_outbox_task() -> dict[str, int]:
    """Run one bounded relay tick; DB leases make duplicate ticks safe."""

    return asyncio.run(dispatch_outbox_once())


@celery_app.task(
    bind=True,
    name="events.consume_document_starts",
    ignore_result=True,
    soft_time_limit=20,
    time_limit=25,
)
def consume_document_starts_task(task: Task) -> dict[str, int]:
    """Run one bounded command-consumer tick on a stable worker identity."""

    hostname = str(getattr(task.request, "hostname", None) or "event-worker")
    consumer = f"document-start:{hostname}"[:120]
    return asyncio.run(consume_document_starts_once(consumer=consumer))


@celery_app.task(
    bind=True,
    name="events.consume_document_lifecycle",
    ignore_result=True,
    soft_time_limit=20,
    time_limit=25,
)
def consume_document_lifecycle_task(task: Task) -> dict[str, int]:
    """Run one bounded lifecycle-projector tick on a stable identity."""

    hostname = str(getattr(task.request, "hostname", None) or "event-worker")
    consumer = f"document-lifecycle:{hostname}"[:120]
    return asyncio.run(consume_document_lifecycle_once(consumer=consumer))


@celery_app.task(
    bind=True,
    name="events.consume_run_commands",
    ignore_result=True,
    soft_time_limit=20,
    time_limit=25,
)
def consume_run_commands_task(task: Task) -> dict[str, int]:
    """Queue one bounded batch of attested durable run commands."""

    hostname = str(getattr(task.request, "hostname", None) or "event-worker")
    consumer = f"run-commands:{hostname}"[:120]
    return asyncio.run(consume_run_commands_once(consumer=consumer))


@celery_app.task(
    bind=True,
    name="runs.execute_next",
    ignore_result=True,
    soft_time_limit=150,
    time_limit=180,
)
def execute_run_task(task: Task) -> str:
    """Execute at most one claimed run on the isolated async runs queue."""

    hostname = str(getattr(task.request, "hostname", None) or "run-worker")
    task_id = str(getattr(task.request, "id", None) or "tick")
    owner = f"run:{hostname}:{task_id}"[:200]
    return asyncio.run(execute_run_once(owner=owner))


@celery_app.task(
    bind=True,
    name="summaries.refresh_next",
    ignore_result=True,
    soft_time_limit=150,
    time_limit=180,
)
def refresh_summary_task(task: Task) -> str:
    """Refresh at most one summary on the isolated background queue."""

    hostname = str(getattr(task.request, "hostname", None) or "summary-worker")
    task_id = str(getattr(task.request, "id", None) or "tick")
    owner = f"summary:{hostname}:{task_id}"[:200]
    return asyncio.run(execute_summary_once(owner=owner))


@celery_app.task(
    bind=True,
    name="evaluations.execute_next",
    ignore_result=True,
    soft_time_limit=540,
    time_limit=570,
)
def execute_evaluation_task(task: Task) -> str:
    """Execute one lease-fenced evaluation case on its isolated queue."""

    hostname = str(getattr(task.request, "hostname", None) or "evaluation-worker")
    task_id = str(getattr(task.request, "id", None) or "tick")
    owner = f"evaluation:{hostname}:{task_id}"[:200]
    return asyncio.run(run_evaluation_once(owner=owner))


@celery_app.task(
    bind=True,
    name="documents.run_durable_stage",
    ignore_result=True,
)
def run_durable_stage_task(task: Task) -> str:
    """Run at most one SQL-fenced stage on the isolated ingestion queue."""

    hostname = str(getattr(task.request, "hostname", None) or "ingestion-worker")
    task_id = str(getattr(task.request, "id", None) or "tick")
    owner = f"durable-stage:{hostname}:{task_id}"[:200]
    return asyncio.run(run_durable_stage_once(owner=owner))


@celery_app.task(
    bind=True,
    name="embeddings.scan_deployment",
    ignore_result=True,
    soft_time_limit=25,
    time_limit=30,
)
def scan_embedding_deployment_task(task: Task) -> dict[str, object]:
    """Provision and discover one bounded page for a pending generation."""

    hostname = str(getattr(task.request, "hostname", None) or "ingestion-worker")
    task_id = str(getattr(task.request, "id", None) or "tick")
    owner = f"embedding-scan:{hostname}:{task_id}"[:200]
    result = asyncio.run(run_deployment_scan_once(owner=owner))
    return {
        "state": result.state,
        "scanned": result.scanned,
        "emitted": result.emitted,
        "scan_complete": result.scan_complete,
    }


@celery_app.task(
    bind=True,
    name="documents.sync_vector_eligibility",
    ignore_result=True,
    soft_time_limit=25,
    time_limit=30,
)
def sync_vector_eligibility_task(task: Task) -> dict[str, object]:
    """Apply one lease-fenced lifecycle projection to active vector storage."""

    hostname = str(getattr(task.request, "hostname", None) or "ingestion-worker")
    task_id = str(getattr(task.request, "id", None) or "tick")
    owner = f"vector-eligibility:{hostname}:{task_id}"[:200]
    result = asyncio.run(run_eligibility_projection_once(owner=owner))
    return {"state": result.state, "revision": result.revision}


class IngestTask(Task):  # type: ignore[misc]
    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        expected_revision = int(args[1]) if len(args) > 1 else 1
        asyncio.run(ingest.mark_failed(UUID(str(args[0])), expected_revision, str(exc)))


def _run(task: Task, coroutine_factory: Any) -> None:
    try:
        asyncio.run(coroutine_factory())
    except IngestFailure:
        raise
    except Exception as exc:
        raise task.retry(
            exc=exc,
            countdown=2**task.request.retries,
        ) from exc


@celery_app.task(
    base=IngestTask,
    bind=True,
    max_retries=_MAX_RETRIES,
    name="documents.parse",
)
def parse_task(task: Task, document_id: str, expected_revision: int = 1) -> str:
    _run(
        task,
        lambda: ingest.run_parse(UUID(document_id), expected_revision),
    )
    return document_id


@celery_app.task(
    base=IngestTask,
    bind=True,
    max_retries=_MAX_RETRIES,
    name="documents.chunk",
)
def chunk_task(task: Task, document_id: str, expected_revision: int = 1) -> str:
    _run(
        task,
        lambda: ingest.run_chunk(UUID(document_id), expected_revision),
    )
    return document_id


@celery_app.task(
    base=IngestTask,
    bind=True,
    max_retries=_MAX_RETRIES,
    name="documents.embed_upsert",
)
def embed_upsert_task(
    task: Task,
    document_id: str,
    expected_revision: int = 1,
) -> str:
    _run(
        task,
        lambda: ingest.run_embed_upsert(UUID(document_id), expected_revision),
    )
    return document_id


@celery_app.task(
    bind=True,
    max_retries=_MAX_RETRIES,
    name="documents.delete",
)
def delete_task(
    task: Task,
    document_id: str,
    actor_id: str | None = None,
) -> None:
    try:
        asyncio.run(
            ingest.run_delete(
                UUID(document_id),
                UUID(actor_id) if actor_id else None,
            )
        )
    except Exception as exc:
        raise task.retry(
            exc=exc,
            countdown=2**task.request.retries,
        ) from exc


def select_queue(size_bytes: int) -> str:
    limit = get_settings().interactive_upload_mb * 1024 * 1024
    return "interactive" if size_bytes < limit else "default"


def build_ingest_chain(
    document_id: str,
    queue: str,
    expected_revision: int,
) -> Any:
    return chain(
        parse_task.si(document_id, expected_revision).set(queue=queue),
        chunk_task.si(document_id, expected_revision).set(queue=queue),
        embed_upsert_task.si(document_id, expected_revision).set(queue=queue),
    )


def build_legacy_ingest_chain(document_id: str, queue: str) -> Any:
    """Build revision-1 envelopes consumable by old and new workers."""

    return chain(
        parse_task.si(document_id).set(queue=queue),
        chunk_task.si(document_id).set(queue=queue),
        embed_upsert_task.si(document_id).set(queue=queue),
    )


def enqueue_ingest(
    document_id: UUID,
    size_bytes: int,
    expected_revision: int,
) -> None:
    queue = select_queue(size_bytes)
    if get_settings().ingest_revision_protocol_v2_enabled:
        workflow = build_ingest_chain(str(document_id), queue, expected_revision)
    elif expected_revision == 1:
        workflow = build_legacy_ingest_chain(str(document_id), queue)
    else:
        raise RuntimeError(
            "worker revision protocol v2 is not enabled; drain legacy workers before cutover"
        )
    workflow.apply_async()


def enqueue_delete(document_id: UUID, actor_id: UUID) -> None:
    delete_task.si(str(document_id), str(actor_id)).apply_async(queue="interactive")
