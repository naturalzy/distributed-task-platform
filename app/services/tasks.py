from __future__ import annotations

from typing import BinaryIO
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from app.models.task import Task, TaskStatus
from app.repositories.tasks import TaskRepository
from app.schemas.task import TaskCreate
from app.services.csv_storage import store_csv


class TaskNotFoundError(Exception):
    pass


class TaskService:
    def __init__(self, tasks: TaskRepository) -> None:
        self.tasks = tasks

    async def create(self, user_id: int, payload: TaskCreate) -> Task:
        task = Task(
            user_id=user_id,
            task_type=payload.task_type,
            status=TaskStatus.PENDING,
            parameters=payload.parameters.model_dump(mode="json"),
        )
        return await self.tasks.create_with_outbox(task)

    async def create_csv(self, user_id: int, source: BinaryIO) -> Task:
        task_id = str(uuid4())
        await run_in_threadpool(store_csv, source, task_id)
        task = Task(
            task_uuid=task_id,
            user_id=user_id,
            task_type="csv_analysis",
            status=TaskStatus.PENDING,
            parameters={"file_id": task_id},
        )
        # Retain the input if commit/refresh fails: the commit may already have succeeded.
        return await self.tasks.create_with_outbox(task)

    async def get_for_user(self, task_uuid: str, user_id: int) -> Task:
        task = await self.tasks.get_for_user(task_uuid, user_id)
        if task is None:
            raise TaskNotFoundError
        return task
