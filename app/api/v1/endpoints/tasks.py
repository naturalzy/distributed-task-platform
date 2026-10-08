from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.db.session import get_db
from app.repositories.tasks import TaskRepository
from app.schemas.task import TaskAccepted, TaskCreate, TaskResultRead, TaskStatusRead
from app.services.csv_storage import CSVUploadTooLarge
from app.services.tasks import TaskNotFoundError, TaskService

router = APIRouter()


def get_task_service(db: Annotated[AsyncSession, Depends(get_db)]) -> TaskService:
    return TaskService(TaskRepository(db))


TaskServiceDependency = Annotated[TaskService, Depends(get_task_service)]


async def find_owned_task(task_id: UUID, user_id: int, service: TaskServiceDependency):
    try:
        return await service.get_for_user(str(task_id), user_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc


@router.post("", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    payload: TaskCreate,
    current_user: CurrentUser,
    service: TaskServiceDependency,
) -> TaskAccepted:
    task = await service.create(current_user.id, payload)
    return TaskAccepted(task_id=task.task_uuid, status=task.status)


@router.post("/csv", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_csv_task(
    file: Annotated[UploadFile, File()],
    current_user: CurrentUser,
    service: TaskServiceDependency,
) -> TaskAccepted:
    try:
        task = await service.create_csv(current_user.id, file.file)
    except CSVUploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="CSV storage is unavailable") from exc
    return TaskAccepted(task_id=task.task_uuid, status=task.status)


@router.get("/{task_id}", response_model=TaskStatusRead)
async def get_task_status(
    task_id: UUID,
    current_user: CurrentUser,
    service: TaskServiceDependency,
) -> TaskStatusRead:
    task = await find_owned_task(task_id, current_user.id, service)
    return TaskStatusRead(
        task_id=task.task_uuid,
        status=task.status,
        task_type=task.task_type,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


@router.get("/{task_id}/result", response_model=TaskResultRead)
async def get_task_result(
    task_id: UUID,
    response: Response,
    current_user: CurrentUser,
    service: TaskServiceDependency,
) -> TaskResultRead:
    task = await find_owned_task(task_id, current_user.id, service)
    if task.status in {"PENDING", "RUNNING"}:
        response.status_code = status.HTTP_202_ACCEPTED
    return TaskResultRead(
        task_id=task.task_uuid,
        status=task.status,
        result=task.result,
        error_message=task.error_message,
    )
