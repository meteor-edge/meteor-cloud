"""Authentication utility tests."""

from __future__ import annotations

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_and_verify() -> None:
    hashed = hash_password("correct-horse-battery")
    assert hashed != "correct-horse-battery"
    assert verify_password("correct-horse-battery", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_access_token_round_trip() -> None:
    settings = Settings(
        _env_file=None,
        JWT_SECRET_KEY="test-secret-key-for-jwt",
        JWT_ALGORITHM="HS256",
        JWT_ACCESS_TOKEN_EXPIRE_MINUTES=5,
    )
    token = create_access_token("user-123", settings=settings)
    payload = decode_access_token(token, settings=settings)

    assert payload["sub"] == "user-123"
    assert "exp" in payload
