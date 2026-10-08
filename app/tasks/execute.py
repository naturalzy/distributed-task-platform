from __future__ import annotations

import logging
import time
from threading import Event, Thread
from typing import Any

from app.celery_app import celery_app
from app.core.config import get_settings
from app.repositories.task_worker import TaskWorkerRepository
from app.schemas.task import SumNumbersParameters
from app.tasks.csv_analysis import CSVAnalysisError, run_csv_analysis


def run_sum_numbers(parameters: dict[str, Any]) -> dict[str, int]:
    validated = SumNumbersParameters.model_validate(parameters)
    if validated.delay_seconds:
        time.sleep(validated.delay_seconds)
    return {"sum": sum(validated.numbers), "count": len(validated.numbers)}


TASK_HANDLERS = {"sum_numbers": run_sum_numbers, "csv_analysis": run_csv_analysis}
logger = logging.getLogger(__name__)


def keep_lease_alive(
    repository: TaskWorkerRepository, task_uuid: str, lease_token: str, stop: Event
) -> None:
    interval = get_settings().task_heartbeat_seconds
    while not stop.wait(interval):
        try:
            if not repository.heartbeat(task_uuid, lease_token):
                logger.warning("Task %s lost its execution lease", task_uuid)
                return
        except Exception:
            # A transient database error must not kill the execution process.
            # If the lease expires, the scanner will retry the task.
            logger.exception("Task %s heartbeat failed", task_uuid)


@celery_app.task(name="tasks.execute", ignore_result=True)
def execute_task(task_uuid: str) -> None:
    repository = TaskWorkerRepository()
    claimed = repository.claim(task_uuid)
    if claimed is None:
        # A duplicate message, canceled record, or already completed task.
        return

    stop = Event()
    heartbeat = Thread(
        target=keep_lease_alive,
        args=(repository, task_uuid, claimed.lease_token, stop),
        daemon=True,
    )
    heartbeat.start()
    try:
        handler = TASK_HANDLERS[claimed.task_type]
        result = handler(claimed.parameters)
    except Exception as exc:
        stop.set()
        heartbeat.join()
        if isinstance(exc, CSVAnalysisError):
            error_message = str(exc)
        elif claimed.task_type == "csv_analysis":
            error_message = f"CSV execution failed: {type(exc).__name__}"
        else:
            error_message = f"Task execution failed: {type(exc).__name__}"
        if not repository.finish_failed(task_uuid, claimed.lease_token, error_message):
            logger.warning("Task %s failed after losing its execution lease", task_uuid)
        raise
    else:
        stop.set()
        heartbeat.join()
        if not repository.finish_success(task_uuid, claimed.lease_token, result):
            logger.warning("Task %s completed after losing its execution lease", task_uuid)
