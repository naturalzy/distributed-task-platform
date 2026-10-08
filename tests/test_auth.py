from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service
from app.core.config import get_settings
from app.main import app
from app.models.user import User
from app.services.auth import AuthService


class InMemoryUserRepository:
    """Exercise HTTP and authentication logic without requiring MySQL in unit tests."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}

    async def get_by_username(self, username: str) -> User | None:
        return self.users.get(username)

    async def get_by_public_id(self, public_id: str) -> User | None:
        return next((user for user in self.users.values() if user.public_id == public_id), None)

    async def get_by_username_or_email(self, username: str, email: str) -> User | None:
        return next(
            (
                user
                for user in self.users.values()
                if user.username == username or user.email == email
            ),
            None,
        )

    async def create(self, user: User) -> User:
        user.public_id = str(uuid.uuid4())
        user.created_at = datetime.now(UTC)
        self.users[user.username] = user
        return user


@pytest.fixture
def auth_client():
    users = InMemoryUserRepository()
    service = AuthService(users)  # type: ignore[arg-type]
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        with TestClient(app) as client:
            yield client, users
    finally:
        app.dependency_overrides.clear()


def register_user(client: TestClient):
    return client.post(
        "/api/v1/auth/register",
        json={
            "username": "Alice",
            "email": "Alice@example.com",
            "password": "correct-horse-battery",
        },
    )


def test_register_login_and_current_user(auth_client) -> None:
    client, users = auth_client
    registration = register_user(client)
    assert registration.status_code == 201
    assert registration.json()["username"] == "alice"
    assert registration.json()["email"] == "alice@example.com"
    assert "password_hash" not in registration.json()
    assert users.users["alice"].password_hash.startswith("$argon2")

    login = client.post(
        "/api/v1/auth/login",
        data={"username": "ALICE", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"

    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["public_id"] == registration.json()["public_id"]


def test_duplicate_registration_and_wrong_password(auth_client) -> None:
    client, _ = auth_client
    assert register_user(client).status_code == 201
    assert register_user(client).status_code == 409
    assert client.post(
        "/api/v1/auth/login",
        data={"username": "alice", "password": "wrong-password"},
    ).status_code == 401


def test_invalid_and_expired_token(auth_client) -> None:
    client, _ = auth_client
    assert register_user(client).status_code == 201

    invalid = client.get("/api/v1/users/me", headers={"Authorization": "Bearer invalid"})
    assert invalid.status_code == 401

    now = datetime.now(UTC)
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "some-user",
            "type": "access",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401

    for claims in (
        {"sub": "some-user", "type": "access", "iat": now},
        {"sub": "some-user", "type": "refresh", "iat": now, "exp": now + timedelta(minutes=5)},
    ):
        token = jwt.encode(claims, settings.secret_key, algorithm="HS256")
        response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_disabled_user_cannot_login_or_use_existing_token(auth_client) -> None:
    client, users = auth_client
    assert register_user(client).status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "alice", "password": "correct-horse-battery"},
    )
    token = login.json()["access_token"]
    users.users["alice"].is_active = False

    assert client.post(
        "/api/v1/auth/login",
        data={"username": "alice", "password": "correct-horse-battery"},
    ).status_code == 403
    assert client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401
