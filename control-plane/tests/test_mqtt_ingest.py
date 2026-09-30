"""Control-plane MQTT ingest applies last-value telemetry."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from tests.conftest import auth_header, create_org_with_owner, create_user

INTERNAL = {"X-MQTT-Internal-Token": get_settings().mqtt_internal_token}


def _register(client: TestClient, db_session: Session) -> dict:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap"},
    ).json()["token"]
    response = client.post(
        "/api/v1/agent/register",
        json={"token": token, "name": "edge-01", "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_ingest_status_updates_device(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    device_id = body["device_id"]
    response = client.post(
        "/internal/mqtt/ingest",
        headers=INTERNAL,
        json={
            "topic": f"devices/{device_id}/status",
            "payload": json.dumps({"status": "online", "agent_version": "0.2.0"}),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    listed = client.get(
        f"/api/v1/organizations/{body['organization_id']}/devices/{device_id}",
        headers=auth_header(client, "owner@example.com"),
    ).json()
    assert listed["mqtt_status"] == "online"


def test_ingest_rejects_missing_token(client: TestClient) -> None:
    response = client.post(
        "/internal/mqtt/ingest",
        json={"topic": "devices/x/status", "payload": "{}"},
    )
    assert response.status_code == 401


def test_ingest_unknown_device_is_ok(client: TestClient) -> None:
    response = client.post(
        "/internal/mqtt/ingest",
        headers=INTERNAL,
        json={
            "topic": f"devices/{uuid.uuid4()}/status",
            "payload": json.dumps({"status": "online"}),
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
