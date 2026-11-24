"""The metrics rules can use: what the agent is known to send, plus what devices actually report."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Float, case, cast, func

from app.devices.models import Device
from app.mqtt.payloads import METRIC_NAME


@dataclass(frozen=True, slots=True)
class MetricSpec:
    field: str
    label: str
    unit: str
    group: str
    description: str
    operator: str = "gt"  # suggestion for a new condition
    threshold: float = 80


KNOWN_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("cpu_percent", "CPU usage", "%", "System", "Processor use over all cores.", "gt", 80),
    MetricSpec("memory_percent", "Memory usage", "%", "System", "Memory in use.", "gt", 80),
    MetricSpec("disk_percent", "Disk usage", "%", "System", "Space used on the root filesystem.", "gt", 85),
    MetricSpec("temperature_c", "Temperature", "°C", "System", "Processor temperature.", "gt", 75),
    MetricSpec("load_1m", "Load (1 min)", "", "System", "Load average over 1 minute.", "gt", 4),
    MetricSpec("load_5m", "Load (5 min)", "", "System", "Load average over 5 minutes.", "gt", 4),
    MetricSpec("load_15m", "Load (15 min)", "", "System", "Load average over 15 minutes.", "gt", 4),
    MetricSpec("uptime_seconds", "Uptime", "s", "System", "Seconds since the device started.", "lt", 300),
    MetricSpec("memory_used_bytes", "Memory used", "B", "System", "Memory in use, in bytes.", "gt", 1e9),
    MetricSpec("memory_total_bytes", "Memory total", "B", "System", "Installed memory, in bytes.", "lt", 1e9),
    MetricSpec("disk_used_bytes", "Disk used", "B", "System", "Space used on the root filesystem.", "gt", 1e10),
    MetricSpec("disk_total_bytes", "Disk total", "B", "System", "Size of the root filesystem.", "lt", 1e10),
    MetricSpec("net_rx_bps", "Network in", "B/s", "Network", "Bytes received per second.", "gt", 1e6),
    MetricSpec("net_tx_bps", "Network out", "B/s", "Network", "Bytes sent per second.", "gt", 1e6),
    MetricSpec("wifi_signal_dbm", "Wi-Fi signal", "dBm", "Network", "Weaker is more negative.", "lt", -75),
    MetricSpec("agent_errors", "Agent errors", "", "Agent", "Errors the agent logged since its last report.", "gt", 0),
)
_KNOWN = {m.field: m for m in KNOWN_METRICS}

# app.<name>.<metric>, per container or service the agent watches.
_APP = re.compile(r"^app\.(?P<app>.+)\.(?P<metric>[a-z_]+)$")
_APP_METRICS: dict[str, tuple[str, str, str, float]] = {
    "up": ("running (1 or 0)", "", "lt", 1),
    "healthy": ("healthy (1 or 0)", "", "lt", 1),
    "cpu": ("CPU", "%", "gt", 80),
    "mem": ("memory", "B", "gt", 5e8),
    "restarts": ("restarts", "", "gt", 3),
    "health_ms": ("health check time", "ms", "gt", 1000),
}


def spec_for(field: str) -> MetricSpec:
    known = _KNOWN.get(field)
    if known is not None:
        return known
    match = _APP.match(field)
    if match:
        name, unit, operator, threshold = _APP_METRICS.get(match["metric"], (match["metric"], "", "gt", 0))
        return MetricSpec(
            field, f"{match['app']}: {name}", unit, "Applications", f"Reported for {match['app']}.", operator, threshold
        )
    return MetricSpec(field, field, "", "Other", "Reported by devices.", "gt", 0)


def numeric_metrics(snapshot: dict[str, Any] | None) -> dict[str, float]:
    """The numeric readings of a device's last metrics message."""
    return {
        key: float(value)
        for key, value in (snapshot or {}).items()
        if isinstance(value, int | float) and not isinstance(value, bool) and METRIC_NAME.match(key)
    }


def reading_matches(field: str, operator: str, value: float, maximum: float | None = None) -> ColumnElement[bool]:
    """SQL: the device's last reading of ``field`` compares true. A missing reading never does."""
    reading = case(
        (func.jsonb_typeof(Device.mqtt_metrics[field]) == "number", cast(Device.mqtt_metrics[field].astext, Float))
    )
    tests = {
        "gt": reading > value,
        "gte": reading >= value,
        "lt": reading < value,
        "lte": reading <= value,
        "eq": reading == value,
        "ne": reading != value,
        "between": reading.between(value, maximum),
    }
    return func.coalesce(tests[operator], False)
