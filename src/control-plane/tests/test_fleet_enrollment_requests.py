"""Tests for device-initiated enrollment request, approval, and claim."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.devices.dependencies import (
    get_enroll_ip_rate_limiter,
    get_enroll_poll_ip_rate_limiter,
    get_enroll_poll_rate_limiter,
    get_enroll_request_rate_limiter,
)
from app.devices.enrollment import EnrollmentService
from app.devices.models import DeviceEnrollmentRequest, EnrollmentApiKey
from app.devices.rate_limit import InMemoryRateLimiter
from app.devices.schemas import AgentEnrollRequest
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def _create_key(client: TestClient, org_id, headers, **extra) -> dict:
    payload = {"name": "Field techs", **extra}
    response = client.post(
        f"/api/v1/organizations/{org_id}/enrollment-keys",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_enroll_request_requires_api_key(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agent/enroll/request",
        json={"name": "edge-01", "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    assert response.status_code == 401


def test_submit_request_then_approve_then_claim(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)

    submitted = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={
            "name": "edge-01",
            "mac_addresses": ["AA:BB:CC:DD:EE:FF"],
            "hostname": "edge-01",
            "architecture": "x86_64",
        },
    )
    assert submitted.status_code == 201, submitted.text
    body = submitted.json()
    assert body["status"] == "pending"
    assert body["claim_secret"].startswith("clm_")
    request_id = body["request_id"]

    pending = client.get(
        f"/api/v1/organizations/{org.id}/enrollment-requests",
        headers=headers,
        params={"status": "pending"},
    )
    assert pending.status_code == 200
    assert len(pending.json()) == 1
    listed_request = pending.json()[0]
    assert listed_request["id"] == request_id
    assert "claim_secret" not in listed_request
    assert listed_request["enrollment_key_id"] == key["id"]
    assert listed_request["enrollment_key_name"] == "Field techs"
    assert listed_request["enrollment_key_prefix"] == key["key_prefix"]
    assert listed_request["mac_addresses"] == ["aa:bb:cc:dd:ee:ff"]

    poll_pending = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": request_id, "claim_secret": body["claim_secret"]},
    )
    assert poll_pending.status_code == 200
    assert poll_pending.json()["status"] == "pending"
    assert poll_pending.json()["device_token"] is None

    approved = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{request_id}/approve",
        headers=headers,
        json={"name": "warehouse-edge-01"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["assigned_name"] == "warehouse-edge-01"

    claimed = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": request_id, "claim_secret": body["claim_secret"]},
    )
    assert claimed.status_code == 200, claimed.text
    claim_body = claimed.json()
    assert claim_body["status"] == "approved"
    assert claim_body["device_token"].startswith("dev_")
    assert claim_body["name"] == "warehouse-edge-01"
    assert claim_body["organization_id"] == str(org.id)

    listed = client.get(f"/api/v1/organizations/{org.id}/devices", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    device = listed.json()["items"][0]
    assert device["name"] == "warehouse-edge-01"
    assert device["mac_addresses"] == ["aa:bb:cc:dd:ee:ff"]

    # Subsequent poll does not re-issue the credential.
    second = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": request_id, "claim_secret": body["claim_secret"]},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "approved"
    assert second.json()["device_token"] is None
    assert second.json()["device_id"] == claim_body["device_id"]


def test_reject_enrollment_request(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)

    submitted = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"name": "edge-01", "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    ).json()

    rejected = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{submitted['request_id']}/reject",
        headers=headers,
        json={"reason": "Unknown hardware"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    polled = client.post(
        "/api/v1/agent/enroll/poll",
        json={
            "request_id": submitted["request_id"],
            "claim_secret": submitted["claim_secret"],
        },
    )
    assert polled.status_code == 200
    assert polled.json()["status"] == "rejected"
    assert polled.json()["rejection_reason"] == "Unknown hardware"
    assert polled.json()["device_token"] is None


def test_poll_rejects_wrong_claim_secret(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    submitted = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    ).json()

    response = client.post(
        "/api/v1/agent/enroll/poll",
        json={
            "request_id": submitted["request_id"],
            "claim_secret": "clm_not-the-right-secret",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_enrollment_request"


def test_developer_cannot_approve(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="dev@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.DEVELOPER)
    owner_headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, owner_headers)
    submitted = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    ).json()

    member_headers = auth_header(client, "dev@example.com")
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{submitted['request_id']}/approve",
        headers=member_headers,
        json={},
    )
    assert response.status_code == 403


def test_expired_request_is_reported_on_poll(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    submitted = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    ).json()

    request = db_session.get(DeviceEnrollmentRequest, UUID(submitted["request_id"]))
    assert request is not None
    request.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    polled = client.post(
        "/api/v1/agent/enroll/poll",
        json={
            "request_id": submitted["request_id"],
            "claim_secret": submitted["claim_secret"],
        },
    )
    assert polled.status_code == 200
    assert polled.json()["status"] == "expired"


def test_invalid_api_key_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": "Bearer key_does-not-exist"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def _enroll(client: TestClient, api_key: str, **inventory) -> dict:
    response = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {api_key}"},
        json=inventory,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_duplicate_submit_reuses_pending_row(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)

    first = _enroll(client, key["api_key"], name="edge-01", mac_addresses=["aa:bb:cc:dd:ee:01"])
    second = _enroll(
        client,
        key["api_key"],
        name="edge-01-retry",
        mac_addresses=["AA:BB:CC:DD:EE:01", "11:22:33:44:55:66"],
    )
    assert second["request_id"] == first["request_id"]
    assert second["claim_secret"] != first["claim_secret"]

    rows = db_session.query(DeviceEnrollmentRequest).all()
    assert len(rows) == 1
    assert rows[0].mac_addresses == ["aa:bb:cc:dd:ee:01"]
    assert rows[0].requested_name == "edge-01-retry"

    stale = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": first["request_id"], "claim_secret": first["claim_secret"]},
    )
    assert stale.status_code == 401


def test_different_primary_macs_create_separate_requests(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)

    first = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    second = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:02"])
    assert first["request_id"] != second["request_id"]
    assert db_session.query(DeviceEnrollmentRequest).count() == 2


def test_blank_identity_is_not_deduped(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)

    first = _enroll(client, key["api_key"], name="a")
    second = _enroll(client, key["api_key"], name="b")
    assert first["request_id"] != second["request_id"]
    assert db_session.query(DeviceEnrollmentRequest).count() == 2


def test_reuse_keeps_approved_unclaimed_status(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    first = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])

    approved = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{first['request_id']}/approve",
        headers=headers,
        json={},
    )
    assert approved.status_code == 200
    assert approved.json()["enrollment_key_name"] == "Field techs"

    retry = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    assert retry["request_id"] == first["request_id"]
    assert retry["status"] == "approved"
    assert retry["claim_secret"] != first["claim_secret"]
    assert db_session.query(DeviceEnrollmentRequest).count() == 1

    claimed = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": retry["request_id"], "claim_secret": retry["claim_secret"]},
    )
    assert claimed.status_code == 200
    assert claimed.json()["device_token"].startswith("dev_")


def _approved_request(client: TestClient, db_session: Session, **inventory) -> tuple:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    first = _enroll(client, key["api_key"], **inventory)
    approved = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{first['request_id']}/approve",
        headers=headers,
        json={},
    )
    assert approved.status_code == 200, approved.text
    return org, headers, key, first


def _assert_original_can_still_claim(client: TestClient, first: dict) -> None:
    claimed = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": first["request_id"], "claim_secret": first["claim_secret"]},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["device_token"].startswith("dev_")


def test_other_key_cannot_take_over_an_approved_request(client: TestClient, db_session: Session) -> None:
    org, headers, _key, first = _approved_request(client, db_session, mac_addresses=["aa:bb:cc:dd:ee:01"])
    other_key = _create_key(client, org.id, headers, name="Other")

    other = _enroll(client, other_key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])

    assert other["request_id"] != first["request_id"]
    assert other["status"] == "pending"
    still_pending = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": other["request_id"], "claim_secret": other["claim_secret"]},
    )
    assert still_pending.json().get("device_token") is None
    _assert_original_can_still_claim(client, first)


def test_changed_mac_does_not_inherit_an_approval(client: TestClient, db_session: Session) -> None:
    _org, _headers, key, first = _approved_request(
        client, db_session, serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:01"]
    )

    other = _enroll(client, key["api_key"], serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:02"])

    assert other["request_id"] != first["request_id"]
    assert other["status"] == "pending"
    _assert_original_can_still_claim(client, first)


def test_changed_serial_does_not_inherit_an_approval(client: TestClient, db_session: Session) -> None:
    _org, _headers, key, first = _approved_request(
        client, db_session, serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:01"]
    )

    other = _enroll(client, key["api_key"], serial_number="SN-2", mac_addresses=["aa:bb:cc:dd:ee:01"])

    assert other["request_id"] != first["request_id"]
    assert other["status"] == "pending"


def test_other_key_reuses_a_pending_request_and_shows_on_it(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    other_key = _create_key(client, org.id, headers, name="Other")

    first = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    second = _enroll(client, other_key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])

    assert second["request_id"] == first["request_id"]
    listed = client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()
    items = listed["items"] if isinstance(listed, dict) else listed
    assert [item["enrollment_key_name"] for item in items] == ["Other"]


def test_concurrent_submissions_create_one_request(client: TestClient, db_session: Session, engine) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key_id = UUID(_create_key(client, org.id, headers)["id"])
    db_session.commit()
    workers = 8
    barrier = Barrier(workers)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def submit(_: int) -> str:
        with factory() as session:
            api_key = session.get(EnrollmentApiKey, key_id)
            payload = AgentEnrollRequest(mac_addresses=["aa:bb:cc:dd:ee:01"])
            barrier.wait(timeout=10)
            return str(EnrollmentService(session).submit_request(api_key=api_key, payload=payload).request_id)

    with ThreadPoolExecutor(workers) as pool:
        request_ids = set(pool.map(submit, range(workers)))

    assert len(request_ids) == 1
    db_session.expire_all()
    assert db_session.query(DeviceEnrollmentRequest).count() == 1


def test_poll_is_capped_per_ip_across_request_ids(client: TestClient) -> None:
    client.app.dependency_overrides[get_enroll_poll_rate_limiter] = lambda: InMemoryRateLimiter(
        limit=10_000, window_seconds=60
    )
    ip_limiter = InMemoryRateLimiter(limit=2, window_seconds=60)
    client.app.dependency_overrides[get_enroll_poll_ip_rate_limiter] = lambda: ip_limiter

    statuses = [
        client.post(
            "/api/v1/agent/enroll/poll",
            json={"request_id": f"00000000-0000-0000-0000-00000000000{index}", "claim_secret": "clm_x"},
        ).status_code
        for index in range(3)
    ]

    assert statuses == [401, 401, 429]


def test_wrong_keys_are_limited_per_ip_before_the_key_lookup(client: TestClient) -> None:
    ip_limiter = InMemoryRateLimiter(limit=2, window_seconds=60)
    client.app.dependency_overrides[get_enroll_ip_rate_limiter] = lambda: ip_limiter

    responses = [
        client.post(
            "/api/v1/agent/enroll/request",
            headers={"Authorization": f"Bearer key_guess-{index}"},
            json={"mac_addresses": ["aa:bb:cc:dd:ee:01"]},
        )
        for index in range(3)
    ]

    assert [r.status_code for r in responses] == [401, 401, 429]
    assert 1 <= int(responses[2].headers["Retry-After"]) <= 60


def test_enroll_check_is_limited_per_ip(client: TestClient) -> None:
    ip_limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    client.app.dependency_overrides[get_enroll_ip_rate_limiter] = lambda: ip_limiter

    first = client.post("/api/v1/agent/enroll/check", headers={"Authorization": "Bearer key_guess"})
    second = client.post("/api/v1/agent/enroll/check", headers={"Authorization": "Bearer key_guess"})
    # GET serves the same check, so it shares the same limit.
    third = client.get("/api/v1/agent/enroll/check", headers={"Authorization": "Bearer key_guess"})

    assert first.status_code == 401
    assert second.status_code == 429
    assert third.status_code == 429


def test_rate_limited_request_says_when_to_retry(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    limiter = InMemoryRateLimiter(limit=1, window_seconds=30)
    client.app.dependency_overrides[get_enroll_request_rate_limiter] = lambda: limiter

    _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    limited = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:02"]},
    )

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 30


def test_pending_requests_are_capped_per_key(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    other = _create_key(client, org.id, headers, name="Other batch")
    capped = get_settings().model_copy(update={"enrollment_max_pending_per_key": 2})
    client.app.dependency_overrides[get_settings] = lambda: capped

    _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:02"])
    third = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:03"]},
    )
    # A device that already has a pending request can still retry (its row is reused).
    retry = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    # Another key has its own allowance.
    _enroll(client, other["api_key"], mac_addresses=["aa:bb:cc:dd:ee:04"])

    assert third.status_code == 429
    assert third.json()["error"]["code"] == "too_many_pending_requests"
    assert third.headers["Retry-After"] == "300"
    assert retry["status"] == "pending"
    assert db_session.query(DeviceEnrollmentRequest).count() == 3


def test_moving_or_reviving_a_request_counts_against_the_cap(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key = _create_key(client, org.id, headers)
    other = _create_key(client, org.id, headers, name="Other batch")
    capped = get_settings().model_copy(update={"enrollment_max_pending_per_key": 1})
    client.app.dependency_overrides[get_settings] = lambda: capped

    _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    moved = _enroll(client, other["api_key"], mac_addresses=["aa:bb:cc:dd:ee:02"])
    # The full key cannot take over the other key's pending request.
    takeover = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:02"]},
    )
    assert takeover.status_code == 429

    # Nor revive its own expired request while another one is pending.
    db_session.expire_all()
    row = db_session.get(DeviceEnrollmentRequest, UUID(moved["request_id"]))
    row.api_key_id = UUID(key["id"])
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    revive = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"mac_addresses": ["aa:bb:cc:dd:ee:02"]},
    )
    assert revive.status_code == 429


def test_requests_without_identity_respect_the_cap_when_concurrent(
    client: TestClient, db_session: Session, engine
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    key_id = UUID(_create_key(client, org.id, headers)["id"])
    db_session.commit()
    settings = get_settings().model_copy(update={"enrollment_max_pending_per_key": 2})
    workers = 6
    barrier = Barrier(workers)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def submit(_: int) -> bool:
        with factory() as session:
            api_key = session.get(EnrollmentApiKey, key_id)
            barrier.wait(timeout=10)
            try:
                EnrollmentService(session, settings).submit_request(api_key=api_key, payload=AgentEnrollRequest())
            except AppError:
                return False
            return True

    with ThreadPoolExecutor(workers) as pool:
        accepted = sum(pool.map(submit, range(workers)))

    assert accepted == 2
    db_session.expire_all()
    assert db_session.query(DeviceEnrollmentRequest).count() == 2
