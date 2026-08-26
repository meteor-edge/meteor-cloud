"""Per-device MQTT topic permissions.

A device may only publish status, metrics, and command results, and may only
subscribe to inbound commands. Wildcards, other devices, and reserved topics
such as telemetry are denied.
"""

from __future__ import annotations

import uuid

DEVICE_USERNAME_PREFIX = "device_"

DEVICE_PUBLISH_SUFFIXES = frozenset({"status", "metrics", "commands/result"})
DEVICE_SUBSCRIBE_SUFFIXES = frozenset({"commands"})


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


def _normalize_action(action: str) -> str | None:
    value = action.strip().lower()
    if value.startswith("pub"):
        return "publish"
    if value.startswith("sub"):
        return "subscribe"
    return None


def _has_wildcard(suffix: str) -> bool:
    return any(part in {"+", "#"} for part in suffix.split("/"))


def can_access_topic(device_id: uuid.UUID, action: str, topic: str) -> bool:
    """Return True when ``device_id`` may perform ``action`` on ``topic``."""
    parsed = parse_device_topic(topic)
    if parsed is None:
        return False
    topic_device_id, suffix = parsed
    if topic_device_id != device_id:
        return False
    if not suffix or _has_wildcard(suffix):
        return False
    direction = _normalize_action(action)
    if direction == "publish":
        return suffix in DEVICE_PUBLISH_SUFFIXES
    if direction == "subscribe":
        return suffix in DEVICE_SUBSCRIBE_SUFFIXES
    return False
