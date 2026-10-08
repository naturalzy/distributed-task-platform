from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import OutboxEvent, Task


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_with_outbox(self, task: Task) -> Task:
        self.session.add(task)
        try:
            await self.session.flush()
            self.session.add(OutboxEvent(task_id=task.id, task_uuid=task.task_uuid))
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        await self.session.refresh(task)
        return task

    async def get_for_user(self, task_uuid: str, user_id: int) -> Task | None:
        statement = select(Task).where(Task.task_uuid == task_uuid, Task.user_id == user_id)
        return await self.session.scalar(statement)

