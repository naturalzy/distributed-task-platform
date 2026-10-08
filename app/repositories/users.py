from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepositoryConflictError(Exception):
    """A database uniqueness constraint rejected the user write."""


class UserRepository:
    """All user table reads and writes live behind this interface."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_username(self, username: str) -> User | None:
        return await self.session.scalar(select(User).where(User.username == username))

    async def get_by_public_id(self, public_id: str) -> User | None:
        return await self.session.scalar(select(User).where(User.public_id == public_id))

    async def get_by_username_or_email(self, username: str, email: str) -> User | None:
        statement = select(User).where(or_(User.username == username, User.email == email))
        return await self.session.scalar(statement)

    async def create(self, user: User) -> User:
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise UserRepositoryConflictError from exc
        await self.session.refresh(user)
        return user
