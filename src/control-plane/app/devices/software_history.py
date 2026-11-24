"""Health history of one workload, read from the data plane (ClickHouse).

Health changes come from the control-plane outbox (``meteor.software``), lifecycle
events and CPU/memory from the agent's device events and ``app.*`` metrics, deploys from
``meteor.deployments``. The caller has already checked that the user may read the device.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deployments.software_models import DeviceSoftware
from app.devices.history import (
    DEPLOYMENTS_STREAM,
    EVENTS_STREAM,
    STATUS_MAX_PAGES,
    STATUS_PAGE_SIZE,
    AppEvent,
    DeploymentHistoryItem,
    _iso,
    _parse_time,
    app_event,
    deployment_items,
    require_data_plane,
)
from app.devices.telemetry import RANGES, TelemetrySeries
from app.history.recorder import SOFTWARE_STREAM
from app.ports.data_plane import DataPlaneApi

SoftwareRange = Literal["24h", "7d", "30d"]
WorkloadHealth = Literal["healthy", "unhealthy", "stopped", "unknown"]

# The data plane reads at most 16 metrics per query: CPU and memory of 8 containers.
MAX_CHARTED = 8
_METRIC = re.compile(r"^[A-Za-z0-9_.:-]{1,50}$")


class HealthSegment(BaseModel):
    start: datetime
    end: datetime
    status: WorkloadHealth


class HealthChange(BaseModel):
    t: datetime
    status: WorkloadHealth
    reason: str | None = None


class SoftwareHistoryResponse(BaseModel):
    software_id: uuid.UUID
    range: SoftwareRange
    start: datetime
    end: datetime
    # Share of the time with a known, not stopped state that the workload was healthy.
    healthy_percent: float | None
    segments: list[HealthSegment]
    changes: list[HealthChange]
    events: list[AppEvent]
    deployments: list[DeploymentHistoryItem]
    bucket_seconds: int
    # <container>.cpu (percent) and <container>.mem (bytes).
    series: list[TelemetrySeries]


def _pages(dp: DataPlaneApi, organization_id: uuid.UUID, params: dict[str, str]) -> list[dict[str, Any]]:
    body = dp.list_events(organization_id=organization_id, params=params)
    events = list(body.get("events", []))
    pages = 1
    while body.get("next_cursor") and pages < STATUS_MAX_PAGES:
        body = dp.list_events(organization_id=organization_id, params={**params, "cursor": body["next_cursor"]})
        events.extend(body.get("events", []))
        pages += 1
    return events


def _status(value: Any) -> WorkloadHealth | None:
    return value if value in ("healthy", "unhealthy", "stopped", "unknown") else None


def health_segments(
    start: datetime, end: datetime, before: WorkloadHealth, changes: list[HealthChange]
) -> list[HealthSegment]:
    """Back-to-back segments covering [start, end]."""
    out: list[HealthSegment] = []
    current, cursor = before, start
    for change in changes:
        if change.status == current:
            continue
        at = min(max(change.t, start), end)
        if at > cursor:
            out.append(HealthSegment(start=cursor, end=at, status=current))
        current, cursor = change.status, at
    if end > cursor:
        out.append(HealthSegment(start=cursor, end=end, status=current))
    return out


def _containers(row: DeviceSoftware) -> list[dict[str, Any]]:
    return list((row.snapshot or {}).get("containers") or [])


def _current(row: DeviceSoftware) -> WorkloadHealth:
    return "stopped" if row.stopped_by_user else (_status(row.health) or "unknown")


def software_history(
    data_plane: DataPlaneApi | None, session: Session, *, row: DeviceSoftware, range_: SoftwareRange, now: datetime
) -> SoftwareHistoryResponse:
    dp = require_data_plane(data_plane)
    window, bucket = RANGES[range_]
    end = now.replace(microsecond=0)
    start = end - window
    org = row.organization_id
    common = {
        "device_id": str(row.device_id),
        "start": _iso(start),
        "end": _iso(end),
        "limit": str(STATUS_PAGE_SIZE),
    }

    raw_changes = [
        event
        for event in _pages(
            dp,
            org,
            {
                **common,
                "stream": SOFTWARE_STREAM,
                "order": "asc",
                "filter": json.dumps({"workload_key": row.workload_key}),
            },
        )
        if (event.get("data") or {}).get("workload_key") == row.workload_key
    ]
    changes: list[HealthChange] = []
    before: WorkloadHealth | None = None
    for event in raw_changes:
        data = event.get("data") or {}
        status, at = _status(data.get("to")), _parse_time(event.get("time"))
        if status is None or at is None:
            continue
        if before is None:
            before = _status(data.get("from")) or "unknown"
        reason = data.get("reason")
        changes.append(HealthChange(t=at, status=status, reason=str(reason)[:300] if reason else None))
    if before is None:
        # No change in the window: the state now is the state all along, from the time it was installed.
        created = row.created_at
        before = _current(row)
        if created > start:
            changes = [HealthChange(t=created, status=before)]
            before = "unknown"
    segments = health_segments(start, end, before, changes)
    # "Stopped" is on purpose and "unknown" is not known: neither counts.
    known = sum((s.end - s.start).total_seconds() for s in segments if s.status in ("healthy", "unhealthy"))
    healthy = sum((s.end - s.start).total_seconds() for s in segments if s.status == "healthy")

    # Older agents send no workload_key on app events: the container names of the snapshot say whose they are.
    names = {str(c.get("name")) for c in _containers(row)}
    events: list[AppEvent] = []
    for event in _pages(dp, org, {**common, "stream": EVENTS_STREAM, "order": "desc"}):
        parsed = app_event(event)
        if parsed is None:
            continue
        key = (event.get("data") or {}).get("workload_key")
        if key == row.workload_key or (key is None and parsed.app in names):
            events.append(parsed)
        if len(events) >= 200:
            break

    deployments = deployment_items(
        session,
        organization_id=org,
        raw=_pages(dp, org, {**common, "stream": DEPLOYMENTS_STREAM, "order": "desc"}),
        workload_key=row.workload_key,
        kind=row.kind,
    )

    metrics = [str(c.get("metric")) for c in _containers(row)]
    metrics = [m for m in metrics if _METRIC.match(m)][:MAX_CHARTED]
    series: list[TelemetrySeries] = []
    bucket_seconds = 0
    if metrics:
        body = dp.get_device_metrics(
            organization_id=org,
            device_id=row.device_id,
            params={
                "start": _iso(start),
                "end": _iso(end),
                "bucket": bucket,
                "names": ",".join(f"app.{m}.{field}" for m in metrics for field in ("cpu", "mem")),
            },
        )
        bucket_seconds = int(body.get("bucket_seconds", 0))
        for item in body.get("series", []):
            one = TelemetrySeries.model_validate(item)
            one.name = one.name.removeprefix("app.")
            if one.points:
                series.append(one)
    return SoftwareHistoryResponse(
        software_id=row.id,
        range=range_,
        start=start,
        end=end,
        healthy_percent=round(healthy * 100 / known, 2) if known > 0 else None,
        segments=segments,
        changes=changes[-200:],
        events=events,
        deployments=deployments[:100],
        bucket_seconds=bucket_seconds,
        series=series,
    )
