"""Ping command create/publish/complete tests."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.models import DeviceCommand
from app.main import create_app
from data_plane.connectivity.mqtt.broker import get_mqtt_publisher
from data_plane.connectivity.mqtt.service import MqttService
from tests.conftest import auth_header, create_org_with_owner, create_user


class CompletingPublisher:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        self.published.append((topic, payload))
        data = json.loads(payload)
        device_id = uuid.UUID(topic.split("/")[1])
        result = json.dumps(
            {
                "command_id": data["command_id"],
                "status": "completed",
                "result": {"message": "pong"},
            }
        )
        MqttService(self.session).apply_command_result(device_id=device_id, payload=result)
        self.session.commit()


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        self.published.append((topic, payload))


def _client_with_publisher(db_session: Session, publisher) -> TestClient:
    application = create_app()

    def override_get_db():
        yield db_session

    from app.core.database import get_db

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_mqtt_publisher] = lambda: publisher
    from app.devices.dependencies import (
        get_enroll_poll_rate_limiter,
        get_enroll_request_rate_limiter,
        get_rate_limiter,
    )
    from app.devices.rate_limit import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(limit=10_000, window_seconds=60)
    application.dependency_overrides[get_rate_limiter] = lambda: limiter
    application.dependency_overrides[get_enroll_request_rate_limiter] = lambda: limiter
    application.dependency_overrides[get_enroll_poll_rate_limiter] = lambda: limiter
    return TestClient(application)


def _register(client: TestClient, db_session: Session) -> tuple[dict, dict]:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap"},
    ).json()["token"]
    body = client.post(
        "/api/v1/agent/register",
        json={"token": token, "name": "edge-01", "mac_addresses": ["aa:bb:cc:dd:ee:01"]},
    ).json()
    return headers, body


def test_ping_published_and_completed(db_session: Session) -> None:
    publisher = CompletingPublisher(db_session)
    client = _client_with_publisher(db_session, publisher)
    headers, body = _register(client, db_session)
    device_id = body["device_id"]
    response = client.post(
        f"/api/v1/organizations/{body['organization_id']}/devices/{device_id}/commands/ping",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["round_trip_ms"] is not None
    assert publisher.published
    topic, message = publisher.published[0]
    assert topic == f"devices/{device_id}/commands"
    assert json.loads(message)["type"] == "ping"
    command = db_session.get(DeviceCommand, uuid.UUID(payload["command_id"]))
    assert command is not None
    assert command.status == "completed"
    assert command.result == {"message": "pong"}


def test_wrong_device_result_rejected(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    _headers, body = _register(client, db_session)
    other = uuid.uuid4()
    command = DeviceCommand(
        organization_id=uuid.UUID(body["organization_id"]),
        device_id=uuid.UUID(body["device_id"]),
        type="ping",
        status="sent",
    )
    db_session.add(command)
    db_session.commit()
    applied = MqttService(db_session).apply_command_result(
        device_id=other,
        payload=json.dumps(
            {
                "command_id": str(command.id),
                "status": "completed",
                "result": {"message": "pong"},
            }
        ),
    )
    assert applied is False
    db_session.refresh(command)
    assert command.status == "sent"


def test_status_message_updates_device(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    _headers, body = _register(client, db_session)
    device_id = uuid.UUID(body["device_id"])
    ok = MqttService(db_session).apply_status_message(
        device_id=device_id,
        payload=json.dumps({"status": "online", "agent_version": "0.2.0"}),
    )
    db_session.commit()
    assert ok is True
    listed = client.get(
        f"/api/v1/organizations/{body['organization_id']}/devices/{device_id}",
        headers=auth_header(client, "owner@example.com"),
    ).json()
    assert listed["mqtt_configured"] is True
    assert listed["mqtt_status"] == "online"


def test_metrics_message_stores_latest_snapshot(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    _headers, body = _register(client, db_session)
    device_id = uuid.UUID(body["device_id"])
    ok = MqttService(db_session).apply_metrics_message(
        device_id=device_id,
        payload=json.dumps(
            {
                "timestamp": "2026-08-25T12:00:00Z",
                "cpu_percent": 18.4,
                "memory_percent": 42.1,
                "disk_percent": 61.3,
                "temperature_c": None,
            }
        ),
    )
    db_session.commit()
    assert ok is True
    listed = client.get(
        f"/api/v1/organizations/{body['organization_id']}/devices/{device_id}",
        headers=auth_header(client, "owner@example.com"),
    ).json()
    assert listed["mqtt_metrics"]["cpu_percent"] == 18.4
    assert listed["mqtt_metrics"]["temperature_c"] is None
    assert listed["mqtt_metrics_at"] is not None
