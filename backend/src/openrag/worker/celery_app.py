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
        task_queues=(Queue("default"), Queue("interactive")),
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = build_celery()
