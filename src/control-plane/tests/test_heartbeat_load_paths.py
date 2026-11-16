"""Heartbeat paths that matter under load (#68): no idle connection between thread hops,
no identity scan when the identity did not change."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.dependencies import get_current_device
from app.devices.repository import DeviceRepository
from tests.conftest import auth_header, create_org_with_owner, create_user

MAC = "aa:bb:cc:dd:ee:01"


def _registered_device(client: TestClient, db_session: Session) -> dict:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "load", "max_uses": 5},
    ).json()["token"]
    response = client.post(
        "/api/v1/agent/register",
        json={"token": token, "serial_number": "SER-1", "mac_addresses": [MAC]},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_device_auth_hands_the_connection_back(client: TestClient, db_session: Session) -> None:
    registration = _registered_device(client, db_session)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=registration["device_token"])

    device = get_current_device(credentials, db_session)

    # The endpoint runs in another thread-pool call; nothing may hold a connection meanwhile.
    assert not db_session.in_transaction()
    assert str(device.id) == registration["device_id"]


def test_unchanged_identity_skips_the_uniqueness_scan(client: TestClient, db_session: Session) -> None:
    registration = _registered_device(client, db_session)
    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    body = {"serial_number": "SER-1", "mac_addresses": [MAC]}

    with patch.object(
        DeviceRepository,
        "list_candidates_for_identity",
        wraps=DeviceRepository.list_candidates_for_identity,
        autospec=True,
    ) as scan:
        assert client.post("/api/v1/agent/heartbeat", headers=device_headers, json=body).status_code == 200
        assert scan.call_count == 0

        changed = {"serial_number": "SER-2", "mac_addresses": [MAC, "aa:bb:cc:dd:ee:02"]}
        assert client.post("/api/v1/agent/heartbeat", headers=device_headers, json=changed).status_code == 200
        assert scan.call_count == 2
