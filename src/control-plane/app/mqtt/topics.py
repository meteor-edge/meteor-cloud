"""MQTT topic checks for the console test UI."""

from __future__ import annotations

from app.core.exceptions import ValidationAppError

_MAX_TOPIC_LEN = 256


def mqtt_topic_matches(filter_topic: str, topic: str) -> bool:
    """Return True when ``topic`` matches an MQTT filter (``+`` / ``#``)."""
    if filter_topic == topic:
        return True
    filter_parts = filter_topic.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(filter_parts):
        if part == "#":
            return index == len(filter_parts) - 1
        if index >= len(topic_parts):
            return False
        if part == "+":
            continue
        if part != topic_parts[index]:
            return False
    return len(filter_parts) == len(topic_parts)


def validate_mqtt_topic(topic: str, *, allow_wildcards: bool) -> str:
    value = topic.strip()
    if not value:
        raise ValidationAppError("mqtt_topic_required", "A topic is required.")
    if len(value) > _MAX_TOPIC_LEN:
        raise ValidationAppError("mqtt_topic_invalid", "Topic is too long.")
    if "\x00" in value or value.startswith("$"):
        raise ValidationAppError("mqtt_topic_invalid", "That MQTT topic is not allowed.")
    parts = value.split("/")
    if any(part == "" for part in parts):
        raise ValidationAppError("mqtt_topic_invalid", "That MQTT topic is not allowed.")
    has_wildcard = any(part in {"+", "#"} for part in parts)
    if has_wildcard and not allow_wildcards:
        raise ValidationAppError("mqtt_topic_invalid", "Wildcards are not allowed when publishing.")
    if "#" in parts and parts[-1] != "#":
        raise ValidationAppError("mqtt_topic_invalid", "That MQTT topic is not allowed.")
    if parts.count("#") > 1:
        raise ValidationAppError("mqtt_topic_invalid", "That MQTT topic is not allowed.")
    return value
