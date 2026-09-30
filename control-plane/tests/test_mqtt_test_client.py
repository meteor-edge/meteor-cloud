"""MQTT test hub and HTTP listen/publish endpoints."""

from __future__ import annotations

import uuid
from queue import Empty

from sqlalchemy.orm import Session

from app.mqtt.hub import MqttEventHub, MqttTestEvent
from app.mqtt.topics import mqtt_topic_matches, validate_mqtt_topic
from app.tenancy.models import Organization, OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_mqtt_commands import RecordingPublisher, _client_with_publisher, _register


def test_mqtt_topic_matches_wildcards() -> None:
    assert mqtt_topic_matches("a/b", "a/b")
    assert mqtt_topic_matches("devices/+/events", "devices/11111111-1111-1111-1111-111111111111/events")
    assert mqtt_topic_matches("lab/#", "lab/temp")
    assert not mqtt_topic_matches("a/b", "a/c")


def test_validate_mqtt_topic_rejects_sys_and_publish_wildcards() -> None:
    assert validate_mqtt_topic("lab/temp", allow_wildcards=False) == "lab/temp"
    try:
        validate_mqtt_topic("$SYS/brokers", allow_wildcards=True)
        raise AssertionError("expected $SYS to fail")
    except Exception as exc:
        assert "not allowed" in str(exc)
    try:
        validate_mqtt_topic("lab/#", allow_wildcards=False)
        raise AssertionError("expected wildcard publish to fail")
    except Exception as exc:
        assert "Wildcard" in str(exc)


def test_hub_only_forwards_matching_topic() -> None:
    hub = MqttEventHub()
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    device_a = uuid.uuid4()
    device_b = uuid.uuid4()
    queue = hub.subscribe(org_a, f"devices/{device_a}/events")
    hub.publish(
        MqttTestEvent(
            organization_id=org_b,
            device_id=device_b,
            topic=f"devices/{device_b}/events",
            payload="other",
            received_at="t0",
        )
    )
    hub.publish(
        MqttTestEvent(
            organization_id=org_a,
            device_id=device_a,
            topic=f"devices/{device_a}/events",
            payload="mine",
            received_at="t1",
        )
    )
    event = queue.get(timeout=1)
    assert event.payload == "mine"
    assert queue.empty()
    hub.unsubscribe(org_a, f"devices/{device_a}/events", queue)


def test_hub_does_not_deliver_unscoped_events_to_org_listeners() -> None:
    hub = MqttEventHub()
    org_id = uuid.uuid4()
    topic = "lab/temp"
    queue = hub.subscribe(org_id, topic)
    hub.publish(
        MqttTestEvent(
            organization_id=None,
            topic=topic,
            payload="unscoped",
            received_at="t0",
        )
    )
    hub.publish(
        MqttTestEvent(
            organization_id=org_id,
            topic=topic,
            payload="scoped",
            received_at="t1",
        )
    )
    assert queue.get(timeout=1).payload == "scoped"
    assert queue.empty()
    hub.unsubscribe(org_id, topic, queue)


def test_hub_does_not_replay_unscoped_events() -> None:
    hub = MqttEventHub()
    org_id = uuid.uuid4()
    topic = "lab/temp"
    hub.publish(
        MqttTestEvent(
            organization_id=None,
            topic=topic,
            payload="unscoped",
            received_at="t0",
        )
    )
    queue = hub.subscribe(org_id, topic)
    assert queue.empty()
    hub.unsubscribe(org_id, topic, queue)


def test_mqtt_publish_requires_membership_and_device(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    headers, body = _register(client, db_session)
    org_id = body["organization_id"]
    device_id = body["device_id"]
    response = client.post(
        f"/api/v1/organizations/{org_id}/mqtt/publish",
        headers=headers,
        json={"device_id": device_id, "payload": {"message": "hello from console"}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["topic"] == f"devices/{device_id}/events"
    assert payload["payload"] == '{"message": "hello from console"}'
    assert publisher.published[0][0] == payload["topic"]

    missing = client.post(
        f"/api/v1/organizations/{org_id}/mqtt/publish",
        headers=headers,
        json={"device_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "device_not_found"


def test_mqtt_publish_plain_topic(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    headers, body = _register(client, db_session)
    org_id = body["organization_id"]
    response = client.post(
        f"/api/v1/organizations/{org_id}/mqtt/publish",
        headers=headers,
        json={"topic": "lab/temp", "payload": "23.5"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"topic": "lab/temp", "payload": "23.5"}
    assert publisher.published[0] == ("lab/temp", "23.5")


def test_mqtt_publish_forbidden_for_viewer(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    headers, body = _register(client, db_session)
    org = db_session.get(Organization, uuid.UUID(body["organization_id"]))
    assert org is not None
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    viewer_headers = auth_header(client, "viewer@example.com")
    denied = client.post(
        f"/api/v1/organizations/{body['organization_id']}/mqtt/publish",
        headers=viewer_headers,
        json={"device_id": body["device_id"]},
    )
    assert denied.status_code == 403


def test_mqtt_events_unknown_device_returns_404(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    headers, body = _register(client, db_session)
    missing = client.get(
        f"/api/v1/organizations/{body['organization_id']}/mqtt/events?device_id={uuid.uuid4()}",
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "device_not_found"


def test_mqtt_events_requires_topic(db_session: Session) -> None:
    publisher = RecordingPublisher()
    client = _client_with_publisher(db_session, publisher)
    headers, body = _register(client, db_session)
    missing = client.get(
        f"/api/v1/organizations/{body['organization_id']}/mqtt/events",
        headers=headers,
    )
    assert missing.status_code == 422


def test_hub_replays_recent_events_on_subscribe() -> None:
    hub = MqttEventHub()
    org_id = uuid.uuid4()
    device_id = uuid.uuid4()
    topic = f"devices/{device_id}/events"
    hub.publish(
        MqttTestEvent(
            organization_id=org_id,
            device_id=device_id,
            topic=topic,
            payload="from-device",
            received_at="t1",
        )
    )
    queue = hub.subscribe(org_id, topic)
    assert queue.get(timeout=1).payload == "from-device"
    hub.unsubscribe(org_id, topic, queue)


def test_hub_drops_events_when_subscriber_queue_is_full() -> None:
    hub = MqttEventHub(max_recent=8, max_queue=2)
    org_id = uuid.uuid4()
    topic = "lab/temp"
    queue = hub.subscribe(org_id, topic)
    for i in range(5):
        hub.publish(
            MqttTestEvent(
                organization_id=org_id,
                topic=topic,
                payload=str(i),
                received_at=str(i),
            )
        )
    got: list[str] = []
    while True:
        try:
            got.append(queue.get_nowait().payload)
        except Empty:
            break
    assert got == ["0", "1"]
    hub.unsubscribe(org_id, topic, queue)


def test_hub_replay_drops_when_queue_is_full() -> None:
    hub = MqttEventHub(max_recent=5, max_queue=2)
    org_id = uuid.uuid4()
    topic = "lab/temp"
    for i in range(5):
        hub.publish(
            MqttTestEvent(
                organization_id=org_id,
                topic=topic,
                payload=str(i),
                received_at=str(i),
            )
        )
    queue = hub.subscribe(org_id, topic)
    got: list[str] = []
    while True:
        try:
            got.append(queue.get_nowait().payload)
        except Empty:
            break
    assert got == ["0", "1"]
    hub.unsubscribe(org_id, topic, queue)
