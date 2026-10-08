from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.sync_session import get_sync_session_factory
from app.models.task import OutboxEvent, Task, TaskStatus


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def recover_one() -> str | None:
    """Requeue one expired RUNNING attempt, or fail it after the retry budget."""
    now = utc_now()
    settings = get_settings()
    with get_sync_session_factory().begin() as session:
        task = session.scalar(
            select(Task)
            .where(
                Task.status == TaskStatus.RUNNING,
                or_(
                    Task.lease_expires_at <= now,
                    # Rows already RUNNING before this migration have no lease.
                    (Task.lease_expires_at.is_(None))
                    & (Task.started_at <= now - timedelta(seconds=settings.task_lease_seconds)),
                ),
            )
            .order_by(Task.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None

        task.lease_token = None
        task.lease_expires_at = None
        if task.execution_attempts >= settings.task_max_attempts:
            task.status = TaskStatus.FAILED
            task.error_message = f"Worker lease expired after {task.execution_attempts} attempts"
            task.finished_at = now
        else:
            event = session.scalar(
                select(OutboxEvent).where(OutboxEvent.task_id == task.id).with_for_update()
            )
            if event is None:
                raise RuntimeError(f"Missing outbox event for task {task.task_uuid}")
            task.status = TaskStatus.PENDING
            task.started_at = None
            task.finished_at = None
            task.result = None
            task.error_message = None
            event.sent_at = None
            event.next_attempt_at = now
            event.last_error = "Worker lease expired; retry scheduled"
        return task.task_uuid
