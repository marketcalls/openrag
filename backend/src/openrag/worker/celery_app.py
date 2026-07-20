from celery import Celery
from celery.signals import beat_init, worker_process_init, worker_process_shutdown
from kombu import Queue

from openrag.core.config import get_settings
from openrag.core.logging import configure_logging
from openrag.core.telemetry import (
    TelemetryRuntime,
    activate_telemetry,
    build_telemetry,
    deactivate_telemetry,
)

_telemetry_runtime: TelemetryRuntime | None = None


def initialize_worker_telemetry(**_kwargs: object) -> None:
    global _telemetry_runtime
    if _telemetry_runtime is not None:
        return
    _telemetry_runtime = build_telemetry(get_settings())
    activate_telemetry(_telemetry_runtime)
    configure_logging(_telemetry_runtime.logger_provider)


def shutdown_worker_telemetry(**_kwargs: object) -> None:
    global _telemetry_runtime
    if _telemetry_runtime is None:
        return
    deactivate_telemetry(_telemetry_runtime)
    _telemetry_runtime.shutdown()
    _telemetry_runtime = None


worker_process_init.connect(initialize_worker_telemetry, weak=False)
worker_process_shutdown.connect(shutdown_worker_telemetry, weak=False)
beat_init.connect(initialize_worker_telemetry, weak=False)


def build_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "openrag",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["openrag.worker.tasks"],
    )
    app.conf.update(
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_default_queue="default",
        task_queues=(
            Queue("default"),
            Queue("interactive"),
            Queue("models"),
            Queue("events"),
            Queue("enrichment"),
            Queue("evaluations"),
            Queue("ingestion"),
            Queue("runs"),
            Queue("summaries"),
        ),
        task_routes={
            "events.*": {"queue": "events"},
            "enrichment.*": {"queue": "enrichment"},
            "evaluations.*": {"queue": "evaluations"},
            "quality.*": {"queue": "evaluations"},
            "models.*": {"queue": "models"},
            "runs.*": {"queue": "runs"},
            "summaries.*": {"queue": "summaries"},
        },
        task_annotations={
            "documents.parse": {
                "soft_time_limit": settings.parser_timeout_seconds + 5,
                "time_limit": (
                    settings.parser_timeout_seconds + settings.parser_hard_timeout_grace_seconds
                ),
            },
            "documents.run_durable_stage": {
                "soft_time_limit": settings.document_stage_soft_time_limit_seconds,
                "time_limit": (
                    settings.document_stage_soft_time_limit_seconds
                    + settings.parser_hard_timeout_grace_seconds
                ),
            },
        },
        worker_max_memory_per_child=settings.parser_worker_max_memory_mb * 1024,
        worker_max_tasks_per_child=settings.parser_worker_max_tasks,
        beat_schedule={
            "dispatch-outbox": {
                "task": "events.dispatch_outbox",
                "schedule": 2.0,
                "options": {"queue": "events", "expires": 5},
            },
            "consume-document-starts": {
                "task": "events.consume_document_starts",
                "schedule": 1.0,
                "options": {"queue": "events", "expires": 5},
            },
            "consume-document-lifecycle": {
                "task": "events.consume_document_lifecycle",
                "schedule": 1.0,
                "options": {"queue": "events", "expires": 5},
            },
            "consume-run-commands": {
                "task": "events.consume_run_commands",
                "schedule": 1.0,
                "options": {"queue": "events", "expires": 5},
            },
            "execute-agent-run": {
                "task": "runs.execute_next",
                "schedule": 0.25,
                "options": {"queue": "runs", "expires": 2},
            },
            "execute-rag-evaluation": {
                "task": "evaluations.execute_next",
                "schedule": 0.5,
                "options": {"queue": "evaluations", "expires": 3},
            },
            "schedule-rag-evaluations": {
                "task": "evaluations.schedule_due",
                "schedule": 60.0,
                "options": {"queue": "evaluations", "expires": 55},
            },
            "audit-answer-quality": {
                "task": "quality.execute_next",
                "schedule": 0.5,
                "options": {"queue": "evaluations", "expires": 3},
            },
            "execute-model-probe": {
                "task": "models.execute_probe",
                "schedule": 0.5,
                "options": {"queue": "models", "expires": 3},
            },
            "execute-document-enrichment": {
                "task": "enrichment.execute_next",
                "schedule": 0.5,
                "options": {"queue": "enrichment", "expires": 3},
            },
            "schedule-document-enrichment": {
                "task": "enrichment.schedule_backfill",
                "schedule": 5.0,
                "options": {"queue": "enrichment", "expires": 10},
            },
            "refresh-conversation-summary": {
                "task": "summaries.refresh_next",
                "schedule": 1.0,
                "options": {"queue": "summaries", "expires": 5},
            },
            "run-durable-document-stage": {
                "task": "documents.run_durable_stage",
                "schedule": 1.0,
                "options": {"queue": "ingestion", "expires": 5},
            },
            "scan-embedding-deployment": {
                "task": "embeddings.scan_deployment",
                "schedule": 1.0,
                "options": {"queue": "ingestion", "expires": 5},
            },
            "sync-vector-eligibility": {
                "task": "documents.sync_vector_eligibility",
                "schedule": 1.0,
                "options": {"queue": "ingestion", "expires": 5},
            },
        },
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = build_celery()
