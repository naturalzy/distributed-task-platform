from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> dict[str, str]:
    """Minimal task used to verify that the worker and broker are connected."""
    return {"status": "ok"}

