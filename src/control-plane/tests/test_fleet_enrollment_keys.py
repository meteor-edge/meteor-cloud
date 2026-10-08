"""Tests for enrollment API key creation, listing, and revocation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def test_create_enrollment_key_returns_plaintext_once(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    created = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Field techs"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["api_key"].startswith("key_")
    assert body["key_prefix"] == body["api_key"][:12]
    assert "device_type_id" not in body
    assert "device_group_id" not in body

    listed = client.get(f"/api/v1/organizations/{org.id}/enrollment-keys", headers=headers)
    assert listed.status_code == 200
    entries = listed.json()
    assert len(entries) == 1
    assert "api_key" not in entries[0]
    assert entries[0]["key_prefix"] == body["key_prefix"]


def test_member_cannot_create_enrollment_key(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.OPERATOR)
    headers = auth_header(client, "member@example.com")

    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Nope"},
    )
    assert response.status_code == 403


def test_revoke_enrollment_key(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Revoke me"},
    ).json()

    revoked = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys/{created['id']}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    response = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {created['api_key']}"},
        json={"name": "edge-01", "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_enroll_check_accepts_valid_key(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Field techs"},
    ).json()

    response = client.get(
        "/api/v1/agent/enroll/check",
        headers={"Authorization": f"Bearer {created['api_key']}"},
    )
    assert response.status_code == 200, response.text
    posted = client.post(
        "/api/v1/agent/enroll/check",
        headers={"Authorization": f"Bearer {created['api_key']}"},
        json={},
    )
    assert posted.status_code == 200, posted.text
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["organization_id"] == str(org.id)
    assert body["organization_name"] == "Acme Energy"
    assert body["key_name"] == "Field techs"
    assert body["key_prefix"] == created["key_prefix"]
    assert "api_key" not in body


def test_enroll_check_rejects_invalid_key(client: TestClient) -> None:
    response = client.get(
        "/api/v1/agent/enroll/check",
        headers={"Authorization": "Bearer key_does-not-exist"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_create_key_rejects_past_expiry(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Expired", "expires_at": past},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_expiry"
