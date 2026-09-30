"""Tests for device registration, credentials, and heartbeat."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import auth_header, create_org_with_owner, create_user


def _create_token(client: TestClient, org_id, headers, **extra) -> str:
    payload = {"name": "Bootstrap", **extra}
    response = client.post(
        f"/api/v1/organizations/{org_id}/registration-tokens",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_register_new_device(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = _create_token(client, org.id, headers)

    response = client.post(
        "/api/v1/agent/register",
        json={
            "token": token,
            "name": "edge-01",
            "mac_addresses": ["AA:BB:CC:DD:EE:FF"],
            "hostname": "edge-01",
            "os_name": "Ubuntu",
            "architecture": "x86_64",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["device_token"].startswith("dev_")
    assert body["organization_id"] == str(org.id)
    assert body["heartbeat_interval_seconds"] == 60

    # Device now visible to the organization.
    listed = client.get(f"/api/v1/organizations/{org.id}/devices", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    device = listed.json()["items"][0]
    assert device["mac_addresses"] == ["aa:bb:cc:dd:ee:ff"]


def test_register_increments_token_use_count(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap", "max_uses": 2},
    ).json()
    token = created["token"]

    client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    listed = client.get(f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers).json()
    assert listed[0]["use_count"] == 1


def test_register_exhausted_token_rejected(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = _create_token(client, org.id, headers, max_uses=1)

    first = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]})
    assert first.status_code == 201
    second = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:02"]})
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "invalid_registration_token"


def test_register_revoked_token_rejected(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap"},
    ).json()
    client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens/{created['id']}/revoke",
        headers=headers,
    )

    response = client.post(
        "/api/v1/agent/register",
        json={"token": created["token"], "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    assert response.status_code == 401


def test_register_invalid_token_rejected(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/agent/register",
        json={"token": "reg_does-not-exist", "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_registration_token"


def test_reregistration_updates_and_rotates_credential(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = _create_token(client, org.id, headers, max_uses=5)

    first = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"], "hostname": "old"},
    ).json()
    second = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"], "hostname": "new"},
    ).json()

    # Same device, rotated credential.
    assert first["device_id"] == second["device_id"]
    assert first["device_token"] != second["device_token"]

    # Only one device exists and inventory was updated.
    listed = client.get(f"/api/v1/organizations/{org.id}/devices", headers=headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["hostname"] == "new"

    # Old credential no longer works.
    old_hb = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {first['device_token']}"},
        json={},
    )
    assert old_hb.status_code == 401


def test_registration_cross_organization_rejected(client: TestClient, db_session: Session) -> None:
    owner_a = create_user(db_session, email="a@example.com")
    owner_b = create_user(db_session, email="b@example.com")
    org_a, _ = create_org_with_owner(db_session, owner_a, slug="org-a")
    org_b, _ = create_org_with_owner(db_session, owner_b, slug="org-b")

    token_a = _create_token(client, org_a.id, auth_header(client, "a@example.com"))
    token_b = _create_token(client, org_b.id, auth_header(client, "b@example.com"))

    client.post("/api/v1/agent/register", json={"token": token_a, "mac_addresses": ["aa:bb:cc:dd:ee:aa"]})
    conflict = client.post("/api/v1/agent/register", json={"token": token_b, "mac_addresses": ["aa:bb:cc:dd:ee:aa"]})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "device_registered_elsewhere"


def test_heartbeat_updates_status(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = _create_token(client, org.id, headers)
    registration = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    ).json()

    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    response = client.post("/api/v1/agent/heartbeat", headers=device_headers, json={})
    assert response.status_code == 200
    assert response.json()["status"] == "online"

    detail = client.get(
        f"/api/v1/organizations/{org.id}/devices/{registration['device_id']}",
        headers=headers,
    ).json()
    assert detail["status"] == "online"
    assert detail["last_seen_at"] is not None


def test_heartbeat_rejects_user_jwt(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    # A user JWT must never be accepted as a device credential.
    response = client.post("/api/v1/agent/heartbeat", headers=headers, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_device_credentials"


def test_heartbeat_rejects_disabled_device(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = _create_token(client, org.id, headers)
    registration = client.post(
        "/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]}
    ).json()

    client.post(
        f"/api/v1/organizations/{org.id}/devices/{registration['device_id']}/disable",
        headers=headers,
    )

    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    response = client.post("/api/v1/agent/heartbeat", headers=device_headers, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "device_disabled"


def test_token_bound_type_and_group_applied(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    device_type = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=headers,
        json={"name": "Gateway"},
    ).json()
    group = client.post(
        f"/api/v1/organizations/{org.id}/device-groups",
        headers=headers,
        json={"name": "Prod"},
    ).json()
    token = _create_token(
        client,
        org.id,
        headers,
        device_type_id=device_type["id"],
        device_group_id=group["id"],
    )

    registration = client.post(
        "/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]}
    ).json()
    detail = client.get(
        f"/api/v1/organizations/{org.id}/devices/{registration['device_id']}",
        headers=headers,
    ).json()
    assert detail["device_type_id"] == device_type["id"]
    assert detail["device_group_id"] == group["id"]
