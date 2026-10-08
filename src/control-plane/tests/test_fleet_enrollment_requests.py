"""Tests for device-initiated enrollment request, approval, and claim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.models import DeviceEnrollmentRequest
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
    assert pending.json()[0]["id"] == request_id
    assert "claim_secret" not in pending.json()[0]

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
