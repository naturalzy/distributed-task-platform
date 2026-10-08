from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.task import TaskStatus


class SumNumbersParameters(BaseModel):
    numbers: list[Annotated[int, Field(ge=-1_000_000, le=1_000_000)]] = Field(
        min_length=1, max_length=1000
    )
    delay_seconds: int = Field(default=0, ge=0, le=15)


class TaskCreate(BaseModel):
    task_type: Literal["sum_numbers"]
    parameters: SumNumbersParameters


class TaskAccepted(BaseModel):
    task_id: str
    status: TaskStatus


class TaskStatusRead(TaskAccepted):
    task_type: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TaskResultRead(TaskAccepted):
    result: dict | None
    error_message: str | None

