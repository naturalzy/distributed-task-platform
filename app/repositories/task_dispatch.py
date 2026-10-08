from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.sync_session import get_sync_session_factory
from app.models.task import OutboxEvent


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def dispatch_one(publish: Callable[[str], None]) -> bool:
    """Publish one locked outbox row; return whether a row was processed."""
    with get_sync_session_factory().begin() as session:
        event = session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.sent_at.is_(None), OutboxEvent.next_attempt_at <= utc_now())
            .order_by(OutboxEvent.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return False

        try:
            publish(event.task_uuid)
        except Exception as exc:
            event.attempts += 1
            event.last_error = f"{type(exc).__name__}: {exc}"[:500]
            event.next_attempt_at = utc_now() + timedelta(
                seconds=min(60, 2 ** min(event.attempts, 6))
            )
        else:
            event.sent_at = utc_now()
            event.last_error = None
        return True

