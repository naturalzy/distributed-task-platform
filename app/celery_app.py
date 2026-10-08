from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "distributed_task_platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.system", "app.tasks.execute"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    task_default_queue="default",
)

