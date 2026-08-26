"""MQTT HTTP authenticate/authorize tests."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.fleet.models import DeviceMqttCredential
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
    body = response.json()
    assert body["mqtt"]["username"].startswith("device_")
    assert body["mqtt"]["password"].startswith("mqtt_")
    assert body["mqtt"]["tls"] is True
    assert body["mqtt"]["port"] == 8883
    return body


def test_valid_mqtt_credential_accepted(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": body["mqtt"]["username"], "password": body["mqtt"]["password"]},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "allow"
    assert response.json()["is_superuser"] is False


def test_invalid_password_rejected(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": body["mqtt"]["username"], "password": "wrong"},
    )
    assert response.json()["result"] == "deny"


def test_unknown_device_rejected(client: TestClient) -> None:
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={
            "username": "device_00000000-0000-0000-0000-000000000000",
            "password": "mqtt_x",
        },
    )
    assert response.json()["result"] == "deny"


def test_disabled_device_rejected(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    owner_headers = auth_header(client, "owner@example.com")
    listed = client.get(
        f"/api/v1/organizations/{body['organization_id']}/devices",
        headers=owner_headers,
    ).json()["items"][0]
    disable = client.post(
        f"/api/v1/organizations/{body['organization_id']}/devices/{listed['id']}/disable",
        headers=owner_headers,
    )
    assert disable.status_code == 200
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": body["mqtt"]["username"], "password": body["mqtt"]["password"]},
    )
    assert response.json()["result"] == "deny"


def test_revoked_mqtt_credential_rejected(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    cred = db_session.get(DeviceMqttCredential, __import__("uuid").UUID(body["device_id"]))
    assert cred is not None
    cred.revoked_at = datetime.now(UTC)
    db_session.add(cred)
    db_session.commit()
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": body["mqtt"]["username"], "password": body["mqtt"]["password"]},
    )
    assert response.json()["result"] == "deny"


def test_internal_endpoints_require_token(client: TestClient) -> None:
    response = client.post(
        "/internal/mqtt/authenticate",
        json={"username": "platform", "password": "x"},
    )
    assert response.status_code == 401


def test_platform_user_is_superuser(client: TestClient) -> None:
    settings = get_settings()
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={
            "username": settings.mqtt_platform_username,
            "password": settings.mqtt_platform_password,
        },
    )
    assert response.json() == {"result": "allow", "is_superuser": True}


def test_authorize_own_and_foreign_topics(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    device_id = body["device_id"]
    username = body["mqtt"]["username"]
    allowed = client.post(
        "/internal/mqtt/authorize",
        headers=INTERNAL,
        json={
            "username": username,
            "action": "subscribe",
            "topic": f"devices/{device_id}/commands",
        },
    )
    denied = client.post(
        "/internal/mqtt/authorize",
        headers=INTERNAL,
        json={
            "username": username,
            "action": "subscribe",
            "topic": "devices/22222222-2222-2222-2222-222222222222/commands",
        },
    )
    assert allowed.json()["result"] == "allow"
    assert denied.json()["result"] == "deny"


def test_authorize_denies_events_and_allows_metrics(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    device_id = body["device_id"]
    username = body["mqtt"]["username"]
    metrics = client.post(
        "/internal/mqtt/authorize",
        headers=INTERNAL,
        json={
            "username": username,
            "action": "publish",
            "topic": f"devices/{device_id}/metrics",
        },
    )
    events = client.post(
        "/internal/mqtt/authorize",
        headers=INTERNAL,
        json={
            "username": username,
            "action": "publish",
            "topic": f"devices/{device_id}/events",
        },
    )
    assert metrics.json()["result"] == "allow"
    assert events.json()["result"] == "deny"


def test_revoke_mqtt_api_rejects_authenticate(client: TestClient, db_session: Session) -> None:
    body = _register(client, db_session)
    owner_headers = auth_header(client, "owner@example.com")
    revoke = client.post(
        f"/api/v1/organizations/{body['organization_id']}/devices/{body['device_id']}/mqtt/revoke",
        headers=owner_headers,
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["mqtt_configured"] is False
    response = client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": body["mqtt"]["username"], "password": body["mqtt"]["password"]},
    )
    assert response.json()["result"] == "deny"
