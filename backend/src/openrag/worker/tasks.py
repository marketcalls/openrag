"""Thin synchronous Celery wrappers over asynchronous ingestion runners."""

import asyncio
from typing import Any
from uuid import UUID

from celery import Task, chain

from openrag.core.config import get_settings
from openrag.modules.documents import ingest
from openrag.modules.documents.pipeline import IngestFailure
from openrag.worker.celery_app import celery_app

_MAX_RETRIES = 3


class IngestTask(Task):  # type: ignore[misc]
    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        asyncio.run(
            ingest.mark_failed(UUID(str(args[0])), int(args[1]), str(exc))
        )


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
def parse_task(task: Task, document_id: str, expected_revision: int) -> str:
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
def chunk_task(task: Task, document_id: str, expected_revision: int) -> str:
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
    expected_revision: int,
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


def enqueue_ingest(
    document_id: UUID,
    size_bytes: int,
    expected_revision: int,
) -> None:
    build_ingest_chain(
        str(document_id),
        select_queue(size_bytes),
        expected_revision,
    ).apply_async()


def enqueue_delete(document_id: UUID, actor_id: UUID) -> None:
    delete_task.si(str(document_id), str(actor_id)).apply_async(
        queue="interactive"
    )
