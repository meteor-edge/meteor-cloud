"""MQTT topic policy: a device can only touch its own topics, on every broker."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.mqtt_brokers import SUPPORTED_BROKERS, MosquittoBroker, mqtt_broker
from app.core.config import Settings, get_settings
from app.mqtt.policy import (
    CONTROL_TOPIC_PREFIX,
    DEVICE_RULES,
    KICK_TOPIC,
    TopicRule,
    device_client_id_allowed,
    evaluate_device,
)
from app.mqtt.runtime import get_mqtt_publisher
from tests.conftest import auth_header, create_org_with_owner, create_user

A = uuid.UUID("11111111-1111-1111-1111-111111111111")
B = uuid.UUID("22222222-2222-2222-2222-222222222222")
TOKEN = get_settings().mqtt_internal_token
INTERNAL = {"X-MQTT-Internal-Token": TOKEN}
VMQ = f"/internal/mqtt/vernemq/{{hook}}?token={TOKEN}"


# --- pure policy -----------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "suffix", "retain", "allowed"),
    [
        ("publish", "status", False, True),
        ("publish", "status", True, True),
        ("publish", "metrics", False, True),
        ("publish", "metrics", True, False),
        ("publish", "commands/result", False, True),
        ("publish", "commands/result", True, False),
        ("subscribe", "commands", False, True),
        ("read", "commands", False, True),
        ("publish", "commands", False, False),  # a device cannot command itself
        ("subscribe", "status", False, False),
        ("subscribe", "commands/result", False, False),
        ("publish", "events", False, False),  # reserved until a rule opens it
        ("subscribe", "events", False, False),
        ("publish", "telemetry", False, False),
        ("delete", "status", False, False),
    ],
)
def test_own_topics(action: str, suffix: str, retain: bool, allowed: bool) -> None:
    assert evaluate_device(A, action, f"devices/{A}/{suffix}", retain=retain).allowed is allowed


@pytest.mark.parametrize("action", ["publish", "subscribe", "read"])
@pytest.mark.parametrize("suffix", ["status", "metrics", "commands", "commands/result"])
def test_other_device_topics_always_denied(action: str, suffix: str) -> None:
    assert not evaluate_device(A, action, f"devices/{B}/{suffix}").allowed


@pytest.mark.parametrize(
    "topic",
    [
        "devices/#",
        "devices/+/commands",
        f"devices/{A}/#",
        f"devices/{A}/+",
        "#",
        "$SYS/broker/clients/connected",
        f"$share/group/devices/{A}/commands",
        KICK_TOPIC,
        f"{CONTROL_TOPIC_PREFIX}anything",
        "fleet/broadcast",
        "",
        f"devices/{A}/status\x00",
        f"DEVICES/{A}/status",
        f"devices/{A}/status/",
        f"/devices/{A}/status",
    ],
)
def test_wildcards_system_and_control_topics_denied(topic: str) -> None:
    for action in ("publish", "subscribe", "read"):
        assert not evaluate_device(A, action, topic).allowed


def test_decision_names_the_rule_or_reason() -> None:
    assert evaluate_device(A, "publish", f"devices/{A}/status").rule == "device-status"
    assert evaluate_device(A, "subscribe", "devices/#").reason == "wildcard"
    assert evaluate_device(A, "publish", KICK_TOPIC).reason == "control_topic"
    assert evaluate_device(A, "publish", f"devices/{B}/status").reason == "no_matching_rule"


def test_rules_are_data_and_can_be_extended() -> None:
    """A later rule set (customer topics, rule engine input) is just more rows."""
    extra = (*DEVICE_RULES, TopicRule("device-events", frozenset({"publish"}), "devices/{device_id}/events"))
    assert evaluate_device(A, "publish", f"devices/{A}/events", rules=extra).allowed
    assert not evaluate_device(A, "publish", f"devices/{B}/events", rules=extra).allowed
    assert not evaluate_device(A, "publish", f"devices/{A}/events").allowed


def test_client_id_must_belong_to_the_device() -> None:
    assert device_client_id_allowed(A, f"device-{A}")
    assert device_client_id_allowed(A, f"device-{A}-mqtt-test-ab12cd34")
    assert not device_client_id_allowed(A, f"device-{B}")
    assert not device_client_id_allowed(A, f"device-{B}-x")
    assert not device_client_id_allowed(A, f"device-{A}x")
    assert not device_client_id_allowed(A, "")
    assert not device_client_id_allowed(A, "anything")
    assert not device_client_id_allowed(A, f"device-{A}-" + "x" * 200)


# --- through the HTTP endpoints -------------------------------------------


def _register(client: TestClient, db_session: Session, email: str, name: str) -> dict:
    owner = create_user(db_session, email=email)
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, email)
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "Bootstrap"}
    ).json()["token"]
    mac = "aa:bb:cc:dd:ee:%02x" % (abs(hash(name)) % 255)
    response = client.post("/api/v1/agent/register", json={"token": token, "name": name, "mac_addresses": [mac]})
    assert response.status_code == 201, response.text
    body = response.json()
    body["headers"] = headers
    return body


def _authorize(client: TestClient, device: dict, action: str, topic: str, **extra: object) -> str:
    response = client.post(
        "/internal/mqtt/authorize",
        headers=INTERNAL,
        json={"username": device["mqtt"]["username"], "action": action, "topic": topic, **extra},
    )
    assert response.status_code == 200
    return response.json()["result"]


def test_cross_tenant_isolation(client: TestClient, db_session: Session) -> None:
    """Devices of two organizations cannot read or write each other's topics."""
    a = _register(client, db_session, "owner-a@example.com", "tenant-a-device")
    b = _register(client, db_session, "owner-b@example.com", "tenant-b-device")
    assert a["organization_id"] != b["organization_id"]
    assert _authorize(client, a, "publish", f"devices/{a['device_id']}/status") == "allow"
    assert _authorize(client, a, "subscribe", f"devices/{a['device_id']}/commands") == "allow"
    for action, suffix in [
        ("publish", "status"),
        ("publish", "metrics"),
        ("publish", "commands"),
        ("publish", "commands/result"),
        ("subscribe", "commands"),
        ("read", "commands"),
    ]:
        assert _authorize(client, a, action, f"devices/{b['device_id']}/{suffix}") == "deny"
        assert _authorize(client, b, action, f"devices/{a['device_id']}/{suffix}") == "deny"


def test_login_with_another_devices_client_id_is_denied(client: TestClient, db_session: Session) -> None:
    a = _register(client, db_session, "owner-a@example.com", "hijack-a")
    b = _register(client, db_session, "owner-b@example.com", "hijack-b")

    def login(clientid: str | None) -> str:
        payload = {"username": a["mqtt"]["username"], "password": a["mqtt"]["password"]}
        if clientid is not None:
            payload["clientid"] = clientid
        return client.post("/internal/mqtt/authenticate", headers=INTERNAL, json=payload).json()["result"]

    assert login(f"device-{a['device_id']}") == "allow"
    assert login(f"device-{a['device_id']}-mqtt-test-1") == "allow"
    assert login(f"device-{b['device_id']}") == "deny"  # would kick device B offline
    assert login("something-else") == "deny"
    assert login(None) == "allow"  # brokers that do not send a client id keep working
    assert (
        _authorize(client, a, "publish", f"devices/{a['device_id']}/status", clientid=f"device-{b['device_id']}")
        == "deny"
    )


def test_retain_flag_from_both_brokers(client: TestClient, db_session: Session) -> None:
    a = _register(client, db_session, "owner-a@example.com", "retain-a")
    metrics = f"devices/{a['device_id']}/metrics"
    assert _authorize(client, a, "publish", metrics, retain=False) == "allow"
    assert _authorize(client, a, "publish", metrics, retain=True) == "deny"  # Mosquitto plugin sends a bool
    assert _authorize(client, a, "publish", metrics, retain="true") == "deny"  # EMQX sends a string
    assert _authorize(client, a, "publish", metrics, retain="false") == "allow"


def test_disable_and_revoke_end_topic_access_and_kick(client: TestClient, db_session: Session) -> None:
    a = _register(client, db_session, "owner-a@example.com", "revoke-a")
    org, device_id, headers = a["organization_id"], a["device_id"], a["headers"]
    status = f"devices/{device_id}/status"

    published: list[tuple[str, str]] = []

    class Recorder:
        def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
            published.append((topic, payload))

        def watch_topic(self, topic: str) -> None: ...

        def unwatch_topic(self, topic: str) -> None: ...

    client.app.dependency_overrides[get_mqtt_publisher] = lambda: Recorder()
    settings = get_settings()
    enabled_before = settings.mqtt_enabled
    settings.mqtt_enabled = True
    try:
        assert _authorize(client, a, "publish", status) == "allow"
        assert (
            client.post(f"/api/v1/organizations/{org}/devices/{device_id}/disable", headers=headers).status_code == 200
        )
        # An already-connected session loses topic access at once.
        assert _authorize(client, a, "publish", status) == "deny"
        assert published == [(KICK_TOPIC, f'{{"username": "device_{device_id}"}}')]

        assert (
            client.post(f"/api/v1/organizations/{org}/devices/{device_id}/enable", headers=headers).status_code == 200
        )
        assert _authorize(client, a, "publish", status) == "allow"

        revoke = client.post(f"/api/v1/organizations/{org}/devices/{device_id}/mqtt/revoke", headers=headers)
        assert revoke.status_code == 200
        assert _authorize(client, a, "publish", status) == "deny"
        assert _authorize(client, a, "subscribe", f"devices/{device_id}/commands") == "deny"
        assert len(published) == 2
        login = client.post(
            "/internal/mqtt/authenticate",
            headers=INTERNAL,
            json={"username": a["mqtt"]["username"], "password": a["mqtt"]["password"]},
        )
        assert login.json()["result"] == "deny"
    finally:
        settings.mqtt_enabled = enabled_before
        client.app.dependency_overrides.pop(get_mqtt_publisher, None)


def test_revoke_survives_a_broken_broker(client: TestClient, db_session: Session) -> None:
    """Broker unreachable while revoking: the API still succeeds and access is gone."""
    a = _register(client, db_session, "owner-a@example.com", "revoke-broker-down")
    org, device_id, headers = a["organization_id"], a["device_id"], a["headers"]

    class Broken:
        def publish(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("broker down")

    client.app.dependency_overrides[get_mqtt_publisher] = lambda: Broken()
    settings = get_settings()
    enabled_before = settings.mqtt_enabled
    settings.mqtt_enabled = True
    try:
        revoke = client.post(f"/api/v1/organizations/{org}/devices/{device_id}/mqtt/revoke", headers=headers)
        assert revoke.status_code == 200
        # Idempotent: a second revoke is fine too.
        again = client.post(f"/api/v1/organizations/{org}/devices/{device_id}/mqtt/revoke", headers=headers)
        assert again.status_code == 200
        assert _authorize(client, a, "publish", f"devices/{device_id}/status") == "deny"
    finally:
        settings.mqtt_enabled = enabled_before
        client.app.dependency_overrides.pop(get_mqtt_publisher, None)


# --- broker adapters -------------------------------------------------------


def test_every_supported_broker_has_an_adapter() -> None:
    for name in SUPPORTED_BROKERS:
        broker = mqtt_broker(Settings(MQTT_PROVIDER=name))
        assert broker.name == name
        assert broker.disconnect_device(A) is False  # no publisher: best effort, never raises
    assert Settings().mqtt_provider == "mosquitto"
    assert isinstance(mqtt_broker(Settings()), MosquittoBroker)
    assert mqtt_broker(Settings()).instant_disconnect is True
    assert mqtt_broker(Settings(MQTT_PROVIDER="vernemq")).clustering is True


def test_unknown_broker_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(MQTT_PROVIDER="rabbitmq")


# --- VerneMQ webhooks ------------------------------------------------------


def _vmq(client: TestClient, hook: str, body: dict) -> dict:
    response = client.post(VMQ.format(hook=hook), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_vernemq_hooks_require_the_token(client: TestClient) -> None:
    for hook in ("auth_on_register", "auth_on_publish", "auth_on_subscribe"):
        assert client.post(f"/internal/mqtt/vernemq/{hook}", json={}).status_code == 401
        assert client.post(f"/internal/mqtt/vernemq/{hook}?token=wrong", json={}).status_code == 401


@pytest.mark.parametrize("suffix", ["", "_m5"])
def test_vernemq_register_publish_subscribe(client: TestClient, db_session: Session, suffix: str) -> None:
    a = _register(client, db_session, "owner-a@example.com", "vmq-a")
    b = _register(client, db_session, "owner-b@example.com", "vmq-b")
    user, password = a["mqtt"]["username"], a["mqtt"]["password"]
    cid = f"device-{a['device_id']}"
    ok, deny = {"result": "ok"}, {"result": {"error": "not_allowed"}}

    register = {"peer_addr": "10.0.0.9", "peer_port": 4242, "mountpoint": "", "clean_session": True}
    assert (
        _vmq(
            client, f"auth_on_register{suffix}", {**register, "username": user, "password": password, "client_id": cid}
        )
        == ok
    )
    assert (
        _vmq(client, f"auth_on_register{suffix}", {**register, "username": user, "password": "nope", "client_id": cid})
        == deny
    )
    assert (
        _vmq(client, f"auth_on_register{suffix}", {**register, "username": None, "password": None, "client_id": cid})
        == deny
    )
    assert (
        _vmq(
            client,
            f"auth_on_register{suffix}",
            {**register, "username": user, "password": password, "client_id": f"device-{b['device_id']}"},
        )
        == deny
    )

    publish = {"username": user, "client_id": cid, "mountpoint": "", "qos": 1, "payload": "e30=", "retain": False}
    assert _vmq(client, f"auth_on_publish{suffix}", {**publish, "topic": f"devices/{a['device_id']}/metrics"}) == ok
    assert _vmq(client, f"auth_on_publish{suffix}", {**publish, "topic": f"devices/{b['device_id']}/metrics"}) == deny
    assert _vmq(client, f"auth_on_publish{suffix}", {**publish, "topic": f"devices/{b['device_id']}/commands"}) == deny
    assert (
        _vmq(
            client,
            f"auth_on_publish{suffix}",
            {**publish, "topic": f"devices/{a['device_id']}/metrics", "retain": True},
        )
        == deny
    )

    subscribe = {"username": user, "client_id": cid, "mountpoint": ""}
    answer = _vmq(
        client,
        f"auth_on_subscribe{suffix}",
        {
            **subscribe,
            "topics": [
                {"topic": f"devices/{a['device_id']}/commands", "qos": 1},
                {"topic": f"devices/{b['device_id']}/commands", "qos": 1},
                {"topic": "devices/#", "qos": 0},
            ],
        },
    )
    assert answer == {
        "result": "ok",
        "topics": [
            {"topic": f"devices/{a['device_id']}/commands", "qos": 1},
            {"topic": f"devices/{b['device_id']}/commands", "qos": 128},
            {"topic": "devices/#", "qos": 128},
        ],
    }
    assert _vmq(client, f"auth_on_subscribe{suffix}", {**subscribe, "topics": []}) == deny


def test_vernemq_platform_user_is_allowed_everywhere(client: TestClient) -> None:
    settings = get_settings()
    user = settings.mqtt_platform_username
    assert _vmq(
        client,
        "auth_on_register",
        {"username": user, "password": settings.mqtt_platform_password, "client_id": "meteorcloud-data-plane-1"},
    ) == {"result": "ok"}
    assert _vmq(
        client,
        "auth_on_subscribe_m5",
        {"username": user, "client_id": "dp", "topics": [{"topic": "$share/dp/devices/+/metrics", "qos": 1}]},
    ) == {"result": "ok", "topics": [{"topic": "$share/dp/devices/+/metrics", "qos": 1}]}
    assert _vmq(
        client, "auth_on_publish", {"username": user, "client_id": "dp", "topic": KICK_TOPIC, "retain": False}
    ) == {"result": "ok"}
