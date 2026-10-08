from __future__ import annotations

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories.users import UserRepository, UserRepositoryConflictError
from app.schemas.auth import TokenResponse
from app.schemas.user import UserCreate


class UserAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InactiveUserError(Exception):
    pass


class AuthService:
    def __init__(self, users: UserRepository) -> None:
        self.users = users

    async def register(self, payload: UserCreate) -> User:
        username = payload.username.lower()
        email = str(payload.email).lower()

        if await self.users.get_by_username_or_email(username, email) is not None:
            raise UserAlreadyExistsError

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(payload.password),
            is_active=True,
        )
        try:
            return await self.users.create(user)
        except UserRepositoryConflictError as exc:
            # The unique constraints remain authoritative if two requests race.
            raise UserAlreadyExistsError from exc

    async def login(self, username: str, password: str) -> TokenResponse:
        user = await self.users.get_by_username(username.strip().lower())
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError
        if not user.is_active:
            raise InactiveUserError
        return TokenResponse(access_token=create_access_token(user.public_id))

    async def get_active_user(self, public_id: str) -> User:
        user = await self.users.get_by_public_id(public_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError
        return user
