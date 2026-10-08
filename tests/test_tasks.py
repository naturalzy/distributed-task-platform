from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.v1.endpoints.tasks import get_task_service
from app.main import app
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.services.tasks import TaskService


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    async def create_with_outbox(self, task: Task) -> Task:
        task.task_uuid = str(uuid.uuid4())
        task.created_at = datetime.now(UTC)
        self.tasks[task.task_uuid] = task
        return task

    async def get_for_user(self, task_uuid: str, user_id: int) -> Task | None:
        task = self.tasks.get(task_uuid)
        return task if task is not None and task.user_id == user_id else None


@pytest.fixture
def task_client():
    repository = InMemoryTaskRepository()
    service = TaskService(repository)  # type: ignore[arg-type]
    user = User(id=1, username="alice", email="alice@example.com", is_active=True)
    app.dependency_overrides[get_task_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client, repository, user
    finally:
        app.dependency_overrides.clear()


def submit_task(client: TestClient):
    return client.post(
        "/api/v1/tasks",
        json={"task_type": "sum_numbers", "parameters": {"numbers": [2, 3, 5]}},
    )


def test_create_status_and_result(task_client) -> None:
    client, repository, _ = task_client
    created = submit_task(client)
    assert created.status_code == 202
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "PENDING"

    status = client.get(f"/api/v1/tasks/{task_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "PENDING"

    pending_result = client.get(f"/api/v1/tasks/{task_id}/result")
    assert pending_result.status_code == 202
    assert pending_result.json()["result"] is None

    task = repository.tasks[task_id]
    task.status = TaskStatus.SUCCESS
    task.result = {"sum": 10, "count": 3}
    completed_result = client.get(f"/api/v1/tasks/{task_id}/result")
    assert completed_result.status_code == 200
    assert completed_result.json()["result"] == {"sum": 10, "count": 3}

    task.status = TaskStatus.FAILED
    task.result = None
    task.error_message = "Task execution failed: ValueError"
    failed_result = client.get(f"/api/v1/tasks/{task_id}/result")
    assert failed_result.status_code == 200
    assert failed_result.json()["error_message"] == "Task execution failed: ValueError"


def test_task_is_private_and_input_is_validated(task_client) -> None:
    client, repository, user = task_client
    task_id = submit_task(client).json()["task_id"]
    user.id = 2
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404
    assert client.get(f"/api/v1/tasks/{task_id}/result").status_code == 404
    assert repository.tasks[task_id].user_id == 1

    invalid = client.post(
        "/api/v1/tasks",
        json={"task_type": "sum_numbers", "parameters": {"numbers": []}},
    )
    assert invalid.status_code == 422

