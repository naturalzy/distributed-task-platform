from __future__ import annotations

import logging
import signal
from threading import Event

from app.celery_app import celery_app
from app.core.config import get_settings
from app.repositories.task_dispatch import dispatch_one
from app.repositories.task_recovery import recover_one

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
stop_event = Event()


def publish_task(task_uuid: str) -> None:
    celery_app.send_task("tasks.execute", args=[task_uuid], queue="default")


def request_shutdown(*_args: object) -> None:
    stop_event.set()


def main() -> None:
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    logger.info("Outbox dispatcher started")
    while not stop_event.is_set():
        try:
            recovered_task = recover_one()
            processed = dispatch_one(publish_task)
        except Exception:
            logger.exception("Outbox dispatch or lease recovery failed")
            stop_event.wait(2)
        else:
            if recovered_task is not None:
                logger.warning("Expired task %s recovered", recovered_task)
            if not processed and recovered_task is None:
                stop_event.wait(get_settings().task_recovery_scan_seconds)
    logger.info("Outbox dispatcher stopped")


if __name__ == "__main__":
    main()

