"""Simulated devices are marked in metadata so the console can show it."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import auth_header, create_org_with_owner, create_user


def _register(client: TestClient, db_session: Session, **inventory):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "Sim"}
    ).json()["token"]
    registered = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["02:aa:bb:cc:dd:01"], "serial_number": "SIM-1", **inventory},
    )
    assert registered.status_code in (200, 201), registered.text
    body = registered.json()
    return org, headers, body["device_id"], {"Authorization": f"Bearer {body['device_token']}"}


def _device(client: TestClient, org, headers, device_id: str) -> dict:
    response = client.get(f"/api/v1/organizations/{org.id}/devices/{device_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_simulated_device_is_marked_at_registration(client: TestClient, db_session: Session) -> None:
    org, headers, device_id, _ = _register(
        client, db_session, simulated=True, hardware_model="Raspberry Pi 5 Model B Rev 1.0"
    )
    assert _device(client, org, headers, device_id)["metadata"]["simulated"] is True


def test_heartbeat_keeps_the_mark_in_step(client: TestClient, db_session: Session) -> None:
    org, headers, device_id, agent = _register(client, db_session)
    assert "simulated" not in _device(client, org, headers, device_id)["metadata"]

    beat = client.post(
        "/api/v1/agent/heartbeat", headers=agent, json={"simulated": True, "capabilities": {"docker": True}}
    )
    assert beat.status_code == 200, beat.text
    metadata = _device(client, org, headers, device_id)["metadata"]
    assert metadata["simulated"] is True
    assert metadata["capabilities"] == {"docker": True}

    # The same device now reports as real hardware (e.g. the profile was switched off).
    client.post("/api/v1/agent/heartbeat", headers=agent, json={})
    assert "simulated" not in _device(client, org, headers, device_id)["metadata"]
