from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import OutboxEvent, Task, User
from app.models.task import TaskStatus
from app.repositories import task_dispatch, task_recovery, task_worker
from app.tasks.execute import TASK_HANDLERS, execute_task


@compiles(BigInteger, "sqlite")
def compile_bigint_as_integer(_type, _compiler, **_kwargs):
    # SQLite requires INTEGER for an autoincrement primary key.
    return "INTEGER"


@pytest.fixture
def task_database(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(task_worker, "get_sync_session_factory", lambda: session_factory)
    monkeypatch.setattr(task_dispatch, "get_sync_session_factory", lambda: session_factory)
    monkeypatch.setattr(task_recovery, "get_sync_session_factory", lambda: session_factory)

    with session_factory.begin() as session:
        user = User(username="worker-test", email="worker@example.com", password_hash="test")
        session.add(user)
        session.flush()
        task = Task(
            user_id=user.id,
            task_type="sum_numbers",
            status=TaskStatus.PENDING,
            parameters={"numbers": [2, 3, 5], "delay_seconds": 0},
        )
        session.add(task)
        session.flush()
        session.add(OutboxEvent(task_id=task.id, task_uuid=task.task_uuid))
        task_uuid = task.task_uuid

    try:
        yield session_factory, task_uuid
    finally:
        engine.dispose()


def test_dispatch_and_worker_persist_success(task_database) -> None:
    session_factory, task_uuid = task_database
    published: list[str] = []

    assert task_dispatch.dispatch_one(published.append) is True
    assert published == [task_uuid]
    assert task_dispatch.dispatch_one(published.append) is False

    execute_task.run(task_uuid)
    execute_task.run(task_uuid)  # Duplicate delivery must not execute again.

    with session_factory() as session:
        task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.task_uuid == task_uuid))
        assert task.status == TaskStatus.SUCCESS
        assert task.result == {"sum": 10, "count": 3}
        assert task.started_at is not None
        assert task.finished_at is not None
        assert task.execution_attempts == 1
        assert task.lease_token is None
        assert event.sent_at is not None


def test_dispatch_retry_and_worker_failure(task_database, monkeypatch) -> None:
    session_factory, task_uuid = task_database

    def unavailable(_task_uuid: str) -> None:
        raise ConnectionError("broker unavailable")

    assert task_dispatch.dispatch_one(unavailable) is True
    with session_factory.begin() as session:
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.task_uuid == task_uuid))
        assert event.attempts == 1
        assert event.sent_at is None
        event.next_attempt_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    assert task_dispatch.dispatch_one(lambda _task_uuid: None) is True

    def failing_handler(_parameters: dict):
        raise ValueError("secret internal detail")

    monkeypatch.setitem(TASK_HANDLERS, "sum_numbers", failing_handler)
    with pytest.raises(ValueError):
        execute_task.run(task_uuid)

    with session_factory() as session:
        task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
        assert task.status == TaskStatus.FAILED
        assert task.error_message == "Task execution failed: ValueError"
        assert "secret internal detail" not in task.error_message


def test_expired_running_task_is_redispatched_and_stale_worker_is_fenced(task_database) -> None:
    session_factory, task_uuid = task_database
    published: list[str] = []
    assert task_dispatch.dispatch_one(published.append) is True

    repository = task_worker.TaskWorkerRepository()
    first_claim = repository.claim(task_uuid)
    assert first_claim is not None
    assert repository.heartbeat(task_uuid, first_claim.lease_token) is True

    # Simulate a killed process: its lease stops being renewed.
    with session_factory.begin() as session:
        task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
        task.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    assert task_recovery.recover_one() == task_uuid
    assert repository.heartbeat(task_uuid, first_claim.lease_token) is False
    assert repository.finish_success(task_uuid, first_claim.lease_token, {"sum": -1}) is False
    assert task_dispatch.dispatch_one(published.append) is True
    assert published == [task_uuid, task_uuid]

    execute_task.run(task_uuid)
    with session_factory() as session:
        task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
        assert task.status == TaskStatus.SUCCESS
        assert task.result == {"sum": 10, "count": 3}
        assert task.execution_attempts == 2


def test_expired_task_fails_after_bounded_attempts(task_database) -> None:
    session_factory, task_uuid = task_database
    repository = task_worker.TaskWorkerRepository()
    for attempt in range(1, 4):
        claim = repository.claim(task_uuid)
        assert claim is not None
        with session_factory.begin() as session:
            task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
            task.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        assert task_recovery.recover_one() == task_uuid
        with session_factory() as session:
            task = session.scalar(select(Task).where(Task.task_uuid == task_uuid))
            if attempt < 3:
                assert task.status == TaskStatus.PENDING
            else:
                assert task.status == TaskStatus.FAILED
                assert task.error_message == "Worker lease expired after 3 attempts"
                assert task.finished_at is not None

    assert repository.claim(task_uuid) is None
    assert task_recovery.recover_one() is None

