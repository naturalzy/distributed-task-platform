from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.sync_session import get_sync_session_factory
from app.models.task import Task, TaskStatus


def utc_now() -> datetime:
    """MySQL DATETIME values are stored as naive UTC timestamps."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class ClaimedTask:
    task_type: str
    parameters: dict[str, Any]
    lease_token: str


class TaskWorkerRepository:
    def claim(self, task_uuid: str) -> ClaimedTask | None:
        now = utc_now()
        token = str(uuid.uuid4())
        with get_sync_session_factory().begin() as session:
            updated = session.execute(
                update(Task)
                .where(Task.task_uuid == task_uuid, Task.status == TaskStatus.PENDING)
                .values(
                    status=TaskStatus.RUNNING,
                    started_at=now,
                    execution_attempts=Task.execution_attempts + 1,
                    lease_token=token,
                    lease_expires_at=now + timedelta(seconds=get_settings().task_lease_seconds),
                )
            )
            if updated.rowcount != 1:
                return None
            task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
            if task is None:
                raise RuntimeError("Claimed task could not be read")
            return ClaimedTask(task.task_type, dict(task.parameters), token)

    def heartbeat(self, task_uuid: str, lease_token: str) -> bool:
        now = utc_now()
        with get_sync_session_factory().begin() as session:
            updated = session.execute(
                update(Task)
                .where(
                    Task.task_uuid == task_uuid,
                    Task.status == TaskStatus.RUNNING,
                    Task.lease_token == lease_token,
                    Task.lease_expires_at > now,
                )
                .values(lease_expires_at=now + timedelta(seconds=get_settings().task_lease_seconds))
            )
            return updated.rowcount == 1

    def finish_success(self, task_uuid: str, lease_token: str, result: dict[str, Any]) -> bool:
        now = utc_now()
        with get_sync_session_factory().begin() as session:
            updated = session.execute(
                update(Task)
                .where(
                    Task.task_uuid == task_uuid,
                    Task.status == TaskStatus.RUNNING,
                    Task.lease_token == lease_token,
                    Task.lease_expires_at > now,
                )
                .values(
                    status=TaskStatus.SUCCESS,
                    result=result,
                    finished_at=now,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            return updated.rowcount == 1

    def finish_failed(self, task_uuid: str, lease_token: str, error_message: str) -> bool:
        now = utc_now()
        with get_sync_session_factory().begin() as session:
            updated = session.execute(
                update(Task)
                .where(
                    Task.task_uuid == task_uuid,
                    Task.status == TaskStatus.RUNNING,
                    Task.lease_token == lease_token,
                    Task.lease_expires_at > now,
                )
                .values(
                    status=TaskStatus.FAILED,
                    error_message=error_message[:500],
                    finished_at=now,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            return updated.rowcount == 1

