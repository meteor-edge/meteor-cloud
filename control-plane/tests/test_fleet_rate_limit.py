"""Tests for device registration rate limiting."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.dependencies import get_rate_limiter
from app.devices.rate_limit import InMemoryRateLimiter
from tests.conftest import auth_header, create_org_with_owner, create_user


def test_registration_is_rate_limited(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap", "max_uses": 100},
    ).json()["token"]

    # Override with a single shared limiter of two requests per window.
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60)
    client.app.dependency_overrides[get_rate_limiter] = lambda: limiter

    first = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]})
    second = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]})
    third = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]})

    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"


def test_in_memory_limiter_allows_within_limit() -> None:
    limiter = InMemoryRateLimiter(limit=3, window_seconds=60)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    # Different identifiers are tracked independently.
    assert limiter.allow("5.6.7.8") is True
