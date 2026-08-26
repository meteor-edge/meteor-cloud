"""MQTT topic ACL unit tests."""

from __future__ import annotations

import uuid

from app.modules.mqtt.acl import can_access_topic, device_id_from_username, mqtt_username_for

DEVICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_username_round_trip() -> None:
    assert device_id_from_username(mqtt_username_for(DEVICE)) == DEVICE
    assert device_id_from_username("platform") is None


def test_device_can_publish_own_status() -> None:
    assert can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/status")


def test_device_can_publish_own_metrics() -> None:
    assert can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/metrics")


def test_device_can_publish_own_command_result() -> None:
    assert can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/commands/result")


def test_device_can_subscribe_own_commands() -> None:
    assert can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/commands")


def test_device_cannot_subscribe_own_events() -> None:
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/events")


def test_device_cannot_publish_events_or_custom_topics() -> None:
    assert not can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/events")
    assert not can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/custom")
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/custom")


def test_device_cannot_publish_or_subscribe_telemetry() -> None:
    assert not can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/telemetry")
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/telemetry")


def test_device_cannot_subscribe_command_result_or_status() -> None:
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/commands/result")
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/status")
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/commands/#")


def test_device_cannot_publish_inbound_commands() -> None:
    assert not can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/commands")


def test_device_cannot_publish_to_another_device() -> None:
    assert not can_access_topic(DEVICE, "publish", f"devices/{OTHER}/status")


def test_device_cannot_subscribe_to_another_device() -> None:
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{OTHER}/commands")


def test_invalid_topic_denied() -> None:
    assert not can_access_topic(DEVICE, "publish", "fleet/broadcast")
    assert not can_access_topic(DEVICE, "subscribe", f"devices/{DEVICE}/#")
    assert not can_access_topic(DEVICE, "publish", f"devices/{DEVICE}/commands")
