"""Tests for registration token creation, listing, and revocation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def test_create_token_returns_plaintext_once(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    created = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Fleet bootstrap", "max_uses": 5},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("reg_")
    assert body["token_prefix"] == body["token"][:12]
    assert body["max_uses"] == 5
    assert body["use_count"] == 0

    listed = client.get(f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers)
    assert listed.status_code == 200
    entries = listed.json()
    assert len(entries) == 1
    # Plaintext is never exposed in list responses.
    assert "token" not in entries[0]
    assert entries[0]["token_prefix"] == body["token_prefix"]


def test_create_token_validates_bound_type_ownership(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    other = create_user(db_session, email="other@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    other_org, _ = create_org_with_owner(db_session, other, slug="other-org")

    other_headers = auth_header(client, "other@example.com")
    foreign_type = client.post(
        f"/api/v1/organizations/{other_org.id}/device-types",
        headers=other_headers,
        json={"name": "Gateway"},
    ).json()

    owner_headers = auth_header(client, "owner@example.com")
    response = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=owner_headers,
        json={"name": "Bad", "device_type_id": foreign_type["id"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "device_type_not_found"


def test_create_token_rejects_past_expiry(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    response = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Expired", "expires_at": past},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_expiry"


def test_member_cannot_create_token(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.MEMBER)
    headers = auth_header(client, "member@example.com")

    response = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Nope"},
    )
    assert response.status_code == 403


def test_revoke_token(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Revoke me"},
    ).json()

    revoked = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens/{created['id']}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
