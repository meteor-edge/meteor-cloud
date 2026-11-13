"""Per-device MQTT identities and topic permissions.

The rules live in :mod:`app.mqtt.policy`. A device may only publish status,
metrics, and command results, and may only subscribe to inbound commands.
Wildcards, other devices, and reserved topics such as telemetry are denied.
"""

from __future__ import annotations

import uuid

from app.mqtt.policy import evaluate_device

DEVICE_USERNAME_PREFIX = "device_"

def mqtt_username_for(device_id: uuid.UUID) -> str:
    return f"{DEVICE_USERNAME_PREFIX}{device_id}"


def device_id_from_username(username: str) -> uuid.UUID | None:
    if not username.startswith(DEVICE_USERNAME_PREFIX):
        return None
    raw = username[len(DEVICE_USERNAME_PREFIX) :]
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def parse_device_topic(topic: str) -> tuple[uuid.UUID, str] | None:
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != "devices":
        return None
    try:
        device_id = uuid.UUID(parts[1])
    except ValueError:
        return None
    return device_id, "/".join(parts[2:])


def can_access_topic(device_id: uuid.UUID, action: str, topic: str, *, retain: bool = False) -> bool:
    """Return True when ``device_id`` may perform ``action`` on ``topic`` (see ``policy``)."""
    return evaluate_device(device_id, action, topic, retain=retain).allowed
