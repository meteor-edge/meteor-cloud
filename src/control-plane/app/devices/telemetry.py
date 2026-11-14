"""Device telemetry history (CPU, memory, disk, temperature) for console charts.

The agent publishes a metrics snapshot every minute over MQTT. The data plane
stores it in ClickHouse (MQTT -> device-ingest -> NATS JetStream ->
clickhouse-writer) and serves bucketed series on its internal API. The control
plane only authorizes the request and picks a bucket for the chosen range; it
never reads ClickHouse itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.core.exceptions import AppError
from app.ports.data_plane import DataPlaneApi

TelemetryRange = Literal["1h", "6h", "24h", "7d", "30d"]

# Range -> (window, bucket). About 60-170 points per chart; the agent reports
# once a minute, so 1m is the finest bucket worth asking for.
RANGES: dict[str, tuple[timedelta, str]] = {
    "1h": (timedelta(hours=1), "1m"),
    "6h": (timedelta(hours=6), "5m"),
    "24h": (timedelta(hours=24), "15m"),
    "7d": (timedelta(days=7), "1h"),
    "30d": (timedelta(days=30), "6h"),
}

# Metrics the console charts. Every meteorcli agent sends the first four; load
# averages come from agent 0.3.4 and later.
DEFAULT_METRICS = ("cpu_percent", "memory_percent", "disk_percent", "temperature_c", "load_1m")


class TelemetryPoint(BaseModel):
    t: datetime
    avg: float
    min: float
    max: float
    count: int


class TelemetrySeries(BaseModel):
    name: str
    points: list[TelemetryPoint]


class DeviceTelemetryResponse(BaseModel):
    device_id: uuid.UUID
    range: TelemetryRange
    start: datetime
    end: datetime
    bucket_seconds: int
    series: list[TelemetrySeries]


def get_device_telemetry(
    data_plane: DataPlaneApi | None,
    *,
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    range_: TelemetryRange,
    now: datetime,
) -> DeviceTelemetryResponse:
    """Caller must already have checked that the user may read the device."""
    if data_plane is None:
        raise AppError(
            "telemetry_unavailable",
            "Telemetry history needs the data API (DATA_PLANE_API_URL and DATA_PLANE_INTERNAL_TOKEN).",
            status_code=503,
        )
    window, bucket = RANGES[range_]
    end = now.replace(microsecond=0)
    start = end - window
    body = data_plane.get_device_metrics(
        organization_id=organization_id,
        device_id=device_id,
        params={
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "bucket": bucket,
            "names": ",".join(DEFAULT_METRICS),
        },
    )
    return DeviceTelemetryResponse(
        device_id=device_id,
        range=range_,
        start=body.get("start", start),
        end=body.get("end", end),
        bucket_seconds=int(body.get("bucket_seconds", 0)),
        series=[TelemetrySeries.model_validate(item) for item in body.get("series", [])],
    )
