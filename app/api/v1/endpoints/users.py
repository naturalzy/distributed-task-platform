from fastapi import APIRouter

from app.api.dependencies import CurrentUser
from app.models.user import User
from app.schemas.user import UserRead

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> User:
    return current_user

