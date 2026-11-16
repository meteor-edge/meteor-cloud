"""Authentication API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from app.core.config import get_settings
from tests.conftest import auth_header, create_user


def test_register_success(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "Owner@Example.com",
            "full_name": "Example Owner",
            "password": "strong-password",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == "owner@example.com"
    assert payload["full_name"] == "Example Owner"
    assert payload["is_active"] is True
    assert "password_hash" not in payload
    assert "password" not in payload


def test_register_duplicate_email(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="owner@example.com")
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "OWNER@example.com",
            "full_name": "Duplicate",
            "password": "strong-password",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_registered"


def test_register_weak_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "weak@example.com",
            "full_name": "Weak",
            "password": "short",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_login_success(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="owner@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "strong-password"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["expires_in"] > 0


def test_login_invalid_password(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="owner@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "strong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_inactive_user(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="inactive@example.com", is_active=False)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "strong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_me_authenticated(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="owner@example.com", full_name="Example Owner")
    headers = auth_header(client, "owner@example.com")
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["email"] == "owner@example.com"
    assert response.json()["full_name"] == "Example Owner"


def test_me_missing_token(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_me_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_me_expired_token(client: TestClient, db_session: Session) -> None:
    user = create_user(db_session, email="owner@example.com")
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "exp": datetime.now(UTC) - timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def _register(client: TestClient, email: str = "jane@example.com", password: str = "correct-horse-1") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Jane", "password": password},
    )
    assert response.status_code == 201, response.text


def test_login_is_rate_limited_per_email(client: TestClient) -> None:
    from app.devices.rate_limit import InMemoryRateLimiter
    from app.identity.dependencies import get_login_email_rate_limiter

    limiter = InMemoryRateLimiter(limit=3, window_seconds=300)
    client.app.dependency_overrides[get_login_email_rate_limiter] = lambda: limiter
    _register(client)
    bad = {"email": "Jane@Example.com", "password": "wrong-password"}
    codes = [client.post("/api/v1/auth/login", json=bad).status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    # The right password is refused too until the window ends: guessing gets no answer.
    blocked = client.post("/api/v1/auth/login", json={"email": "jane@example.com", "password": "correct-horse-1"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.headers.get("Retry-After")


def test_login_is_rate_limited_per_source_ip(client: TestClient) -> None:
    from app.devices.rate_limit import InMemoryRateLimiter
    from app.identity.dependencies import get_login_ip_rate_limiter

    limiter = InMemoryRateLimiter(limit=2, window_seconds=300)
    client.app.dependency_overrides[get_login_ip_rate_limiter] = lambda: limiter
    codes = [
        client.post("/api/v1/auth/login", json={"email": f"user{i}@example.com", "password": "x" * 12}).status_code
        for i in range(3)
    ]
    assert codes == [401, 401, 429]


def test_signup_can_be_turned_off(client: TestClient) -> None:
    from app.core.config import Settings, get_settings

    assert client.get("/api/v1/auth/config").json() == {"signup_enabled": True}
    client.app.dependency_overrides[get_settings] = lambda: Settings(ALLOW_SIGNUP=False)
    try:
        assert client.get("/api/v1/auth/config").json() == {"signup_enabled": False}
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "full_name": "New", "password": "correct-horse-1"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "signup_disabled"
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
