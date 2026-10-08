from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": subject,
        "type": "access",
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Return the subject or raise InvalidTokenError for an unusable token."""
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "type", "iat", "exp"]},
    )
    subject = payload["sub"]
    if payload["type"] != "access" or not isinstance(subject, str) or not subject:
        raise InvalidTokenError("Invalid access token claims")
    return subject
