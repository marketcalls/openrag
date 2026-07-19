from celery import Celery
from kombu import Queue

from openrag.core.config import get_settings


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
        task_queues=(Queue("default"), Queue("interactive"), Queue("events")),
        task_routes={"events.*": {"queue": "events"}},
        beat_schedule={
            "dispatch-outbox": {
                "task": "events.dispatch_outbox",
                "schedule": 2.0,
                "options": {"queue": "events", "expires": 5},
            }
        },
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = build_celery()
