"""Device-management and identity metrics."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.dependencies import get_enroll_poll_rate_limiter, get_rate_limiter
from app.devices.rate_limit import InMemoryRateLimiter
from tests.conftest import auth_header, create_org_with_owner, create_user
from tests.metrics_helpers import sample


def _owner(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, "owner@example.com")


def _token(client: TestClient, org_id, headers) -> str:
    response = client.post(
        f"/api/v1/organizations/{org_id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap", "max_uses": 10},
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_registration_outcomes_are_counted(client: TestClient, db_session: Session) -> None:
    org, headers = _owner(client, db_session)
    token = _token(client, org.id, headers)
    before = {
        result: sample("meteorcloud_device_registrations_total", result=result)
        for result in ("created", "reregistered", "invalid_token")
    }

    body = {"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]}
    assert client.post("/api/v1/agent/register", json=body).status_code == 201
    assert client.post("/api/v1/agent/register", json=body).status_code == 201
    bad = {"token": "rt_not-a-token", "mac_addresses": ["aa:bb:cc:dd:ee:02"]}
    assert client.post("/api/v1/agent/register", json=bad).status_code == 401

    for result in before:
        after = sample("meteorcloud_device_registrations_total", result=result)
        assert after - before[result] == 1, result


def test_heartbeats_are_counted(client: TestClient, db_session: Session) -> None:
    org, headers = _owner(client, db_session)
    token = _token(client, org.id, headers)
    registered = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:03"]})
    device_headers = {"Authorization": f"Bearer {registered.json()['device_token']}"}
    before = sample("meteorcloud_agent_heartbeats_total")

    assert client.post("/api/v1/agent/heartbeat", headers=device_headers, json={}).status_code == 200
    assert client.post("/api/v1/agent/heartbeat", headers=device_headers, json={}).status_code == 200

    assert sample("meteorcloud_agent_heartbeats_total") - before == 2


def test_enrollment_requests_and_polls_are_counted(client: TestClient, db_session: Session) -> None:
    org, headers = _owner(client, db_session)
    key = client.post(f"/api/v1/organizations/{org.id}/enrollment-keys", headers=headers, json={"name": "k"}).json()
    submitted_before = sample("meteorcloud_enrollment_requests_total", result="submitted")
    pending_before = sample("meteorcloud_enrollment_polls_total", status="pending")
    unauthorized_before = sample("meteorcloud_enrollment_polls_total", status="unauthorized")

    submitted = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"name": "edge-01", "mac_addresses": ["aa:bb:cc:dd:ee:04"]},
    ).json()
    client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": submitted["request_id"], "claim_secret": submitted["claim_secret"]},
    )
    client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": submitted["request_id"], "claim_secret": "clm_wrong"},
    )

    assert sample("meteorcloud_enrollment_requests_total", result="submitted") - submitted_before == 1
    assert sample("meteorcloud_enrollment_polls_total", status="pending") - pending_before == 1
    assert sample("meteorcloud_enrollment_polls_total", status="unauthorized") - unauthorized_before == 1


def test_rate_limited_requests_are_counted(client: TestClient, db_session: Session) -> None:
    org, headers = _owner(client, db_session)
    token = _token(client, org.id, headers)
    registration_limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    poll_limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    client.app.dependency_overrides[get_rate_limiter] = lambda: registration_limiter
    client.app.dependency_overrides[get_enroll_poll_rate_limiter] = lambda: poll_limiter
    poll_body = {"request_id": "00000000-0000-0000-0000-000000000000", "claim_secret": "clm_x"}
    registration_before = sample("meteorcloud_rate_limited_total", limiter="registration")
    poll_before = sample("meteorcloud_rate_limited_total", limiter="enrollment_poll")

    assert client.post("/api/v1/agent/register", json={"token": token}).status_code == 201
    assert client.post("/api/v1/agent/register", json={"token": token}).status_code == 429
    client.post("/api/v1/agent/enroll/poll", json=poll_body)
    assert client.post("/api/v1/agent/enroll/poll", json=poll_body).status_code == 429

    assert sample("meteorcloud_rate_limited_total", limiter="registration") - registration_before == 1
    assert sample("meteorcloud_rate_limited_total", limiter="enrollment_poll") - poll_before == 1


def test_logins_are_counted(client: TestClient, db_session: Session) -> None:
    create_user(db_session, email="user@example.com")
    success_before = sample("meteorcloud_auth_logins_total", result="success")
    failure_before = sample("meteorcloud_auth_logins_total", result="failure")

    ok = client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "strong-password"})
    bad = client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "wrong-password"})

    assert ok.status_code == 200
    assert bad.status_code == 401
    assert sample("meteorcloud_auth_logins_total", result="success") - success_before == 1
    assert sample("meteorcloud_auth_logins_total", result="failure") - failure_before == 1
