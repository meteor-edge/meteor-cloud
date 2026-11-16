"""Authentication API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
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


def test_logout_everywhere_ends_existing_tokens(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="owner@example.com")
    first = auth_header(client, "owner@example.com")
    second = auth_header(client, "owner@example.com")
    assert client.get("/api/v1/auth/me", headers=first).status_code == 200

    assert client.post("/api/v1/auth/logout-all", headers=first).status_code == 204
    for headers in (first, second):
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401
    # A new sign-in works again.
    assert client.get("/api/v1/auth/me", headers=auth_header(client, "owner@example.com")).status_code == 200


def test_token_without_version_claim_is_accepted_until_versions_move(client: TestClient, db_session: Session) -> None:
    # Tokens issued before this change have no "ver"; they stay valid for their 30 minutes.
    user = create_user(db_session, email="owner@example.com")
    settings = get_settings()
    legacy = jwt.encode(
        {"sub": str(user.id), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    headers = {"Authorization": f"Bearer {legacy}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    client.post("/api/v1/auth/logout-all", headers=headers)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def _register(client: TestClient, email: str = "jane@example.com", password: str = "correct-horse-1") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Jane", "password": password},
    )
    assert response.status_code == 201, response.text


def test_login_is_rate_limited_per_email(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.devices.rate_limit import InMemoryRateLimiter
    from app.identity.dependencies import get_login_email_rate_limiter

    monkeypatch.setattr("app.devices.rate_limit.time.time", lambda: 1_800_000_000.0)
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


def test_login_is_rate_limited_per_source_ip(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.devices.rate_limit import InMemoryRateLimiter
    from app.identity.dependencies import get_login_ip_rate_limiter

    monkeypatch.setattr("app.devices.rate_limit.time.time", lambda: 1_800_000_000.0)
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


@pytest.mark.parametrize("prefix", ["login_email_rl", "login_ip_rl"])
@pytest.mark.parametrize("operation", ["incr", "expire"])
def test_login_counts_locally_when_redis_counters_fail(
    monkeypatch: pytest.MonkeyPatch, prefix: str, operation: str
) -> None:
    from unittest.mock import Mock

    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.core.config import Settings
    from app.identity import dependencies

    redis_client = Mock()
    redis_client.incr.return_value = 1
    getattr(redis_client, operation).side_effect = RedisConnectionError("unavailable")
    monkeypatch.setattr("redis.Redis.from_url", lambda *_: redis_client)
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: Settings(CACHE_PROVIDER="redis", LOGIN_RATE_LIMIT_REQUESTS=2, LOGIN_IP_RATE_LIMIT_REQUESTS=2),
    )
    builder = (
        dependencies._build_login_email_rate_limiter
        if prefix == "login_email_rl"
        else dependencies._build_login_ip_rate_limiter
    )
    limiter = builder.__wrapped__()
    # Redis is down: the limit still holds, counted in this process.
    assert [limiter.allow("test") for _ in range(3)] == [True, True, False]
    assert limiter.allow("other") is True
    assert redis_client.incr.call_args_list[0].args[0].startswith(f"{prefix}:test:")
