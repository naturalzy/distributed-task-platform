from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import get_auth_service
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.schemas.user import UserCreate, UserRead
from app.services.auth import (
    AuthService,
    InactiveUserError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
)

router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    try:
        return await auth_service.register(payload)
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from exc


@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    try:
        return await auth_service.login(form.username, form.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is disabled",
        ) from exc
