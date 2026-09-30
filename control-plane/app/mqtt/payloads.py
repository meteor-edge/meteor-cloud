"""Validation for MQTT status and metrics payloads."""

from __future__ import annotations

import json
from typing import Any

_STATUS_VALUES = frozenset({"online", "offline"})
_PERCENT_KEYS = ("cpu_percent", "memory_percent", "disk_percent")
_TEMP_MIN = -40.0
_TEMP_MAX = 150.0


def parse_json_object(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_status_payload(payload: str) -> str | None:
    """Return ``online`` or ``offline`` when the payload is acceptable."""
    data = parse_json_object(payload)
    status = data.get("status")
    if not isinstance(status, str) or status not in _STATUS_VALUES:
        return None
    return status


def parse_metrics_payload(payload: str) -> dict[str, Any] | None:
    """Return a sanitized metrics snapshot, or None when the payload is unusable."""
    data = parse_json_object(payload)
    if not data:
        return None
    snapshot: dict[str, Any] = {}
    timestamp = data.get("timestamp")
    if isinstance(timestamp, str) and timestamp.strip():
        snapshot["timestamp"] = timestamp.strip()
    for key in _PERCENT_KEYS:
        value = _optional_percent(data.get(key))
        if value is not None:
            snapshot[key] = value
    temp = data.get("temperature_c")
    if temp is None:
        snapshot["temperature_c"] = None
    else:
        parsed = _optional_float(temp)
        if parsed is not None and _TEMP_MIN <= parsed <= _TEMP_MAX:
            snapshot["temperature_c"] = parsed
    if not any(key in snapshot for key in (*_PERCENT_KEYS, "temperature_c", "timestamp")):
        return None
    return snapshot


def _optional_percent(value: object) -> float | None:
    parsed = _optional_float(value)
    if parsed is None or parsed < 0 or parsed > 100:
        return None
    return parsed


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
