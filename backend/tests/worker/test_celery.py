from openrag.worker.celery_app import celery_app
from openrag.worker.tasks import build_ingest_chain, select_queue


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
    assert all(
        task.options.get("queue") == "interactive"
        for task in signature.tasks
    )
    assert all(task.args == ("doc-id-123", 7) for task in signature.tasks)
