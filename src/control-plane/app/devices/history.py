"""Fleet and device history for the console, read from the data plane (ClickHouse).

- Devices online over time: distinct devices that sent metrics per bucket.
- Device availability: online/offline changes (agent online message, broker last will).
- Deployment history: every status change of the device in a Deploy (control-plane outbox).
- Apps: the latest per-app readings and recent lifecycle events sent by the agent.
- Log history: system warnings (journal, warning or worse) kept long term and searchable.

The control plane authorizes, the data plane reads. Nothing here touches ClickHouse.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.deployments.models import Deployment
from app.devices.telemetry import RANGES, TelemetryRange
from app.ports.data_plane import DataPlaneApi
from app.releases.models import Release

STATUS_STREAM = "meteor.device.status"
EVENTS_STREAM = "meteor.device.events"
METRICS_STREAM = "meteor.device.metrics"
LOGS_STREAM = "meteor.device.logs"
DEPLOYMENTS_STREAM = "meteor.deployments"

# Online-count buckets: wide enough that a device reporting once a minute is
# counted in every bucket it was connected for.
STATUS_PAGE_SIZE = 1000
STATUS_MAX_PAGES = 10

ONLINE_BUCKETS: dict[str, str] = {"1h": "5m", "6h": "5m", "24h": "15m", "7d": "1h", "30d": "6h"}


def require_data_plane(data_plane: DataPlaneApi | None) -> DataPlaneApi:
    if data_plane is None:
        raise AppError(
            "telemetry_unavailable",
            "History needs the data API (DATA_PLANE_API_URL and DATA_PLANE_INTERNAL_TOKEN).",
            status_code=503,
        )
    return data_plane


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ------------------------------------------------------------------ fleet online


class OnlineBucket(BaseModel):
    t: datetime
    devices: int


class FleetOnlineResponse(BaseModel):
    range: TelemetryRange
    start: datetime
    end: datetime
    bucket_seconds: int
    total_devices: int
    online_now: int
    buckets: list[OnlineBucket]


def fleet_online_history(
    data_plane: DataPlaneApi | None,
    *,
    organization_id: uuid.UUID,
    range_: TelemetryRange,
    total_devices: int,
    online_now: int,
    now: datetime,
) -> FleetOnlineResponse:
    dp = require_data_plane(data_plane)
    window, _ = RANGES[range_]
    end = now.replace(microsecond=0)
    start = end - window
    body = dp.online_devices(
        organization_id=organization_id,
        params={"start": _iso(start), "end": _iso(end), "bucket": ONLINE_BUCKETS[range_]},
    )
    return FleetOnlineResponse(
        range=range_,
        start=body.get("start", start),
        end=body.get("end", end),
        bucket_seconds=int(body.get("bucket_seconds", 0)),
        total_devices=total_devices,
        online_now=online_now,
        buckets=[OnlineBucket.model_validate(item) for item in body.get("buckets", [])],
    )


# ------------------------------------------------------------------ availability

Connectivity = Literal["online", "offline", "unknown"]


class AvailabilitySegment(BaseModel):
    start: datetime
    end: datetime
    status: Connectivity


class StatusChange(BaseModel):
    t: datetime
    status: Literal["online", "offline"]


class DeviceAvailabilityResponse(BaseModel):
    device_id: uuid.UUID
    range: TelemetryRange
    start: datetime
    end: datetime
    # Share of the known part of the window the device was online; null when nothing is known.
    uptime_percent: float | None
    offline_count: int
    segments: list[AvailabilitySegment]
    changes: list[StatusChange]


def _status_of(event: dict[str, Any]) -> str | None:
    status = (event.get("data") or {}).get("status")
    return status if status in ("online", "offline") else None


# Broker last-will "offline" often lands within a second of a fresh "online" when
# the agent reconnects (old session cleanup). Drop that race so the strip does
# not stick on yellow while the device is up.
STATUS_LWT_RACE = timedelta(seconds=2)


def debounce_status_changes(
    changes: list[tuple[datetime, str]], *, race_window: timedelta = STATUS_LWT_RACE
) -> list[tuple[datetime, str]]:
    """Keep order; drop offline that arrives immediately after the latest online."""
    out: list[tuple[datetime, str]] = []
    last_online_at: datetime | None = None
    for at, status in sorted(changes, key=lambda change: change[0]):
        if status == "offline" and last_online_at is not None and at - last_online_at <= race_window:
            continue
        if status == "online":
            last_online_at = at
            if out and out[-1][1] == "online":
                continue
        elif out and out[-1][1] == status:
            continue
        else:
            last_online_at = None
        out.append((at, status))
    return out


def build_segments(
    start: datetime, end: datetime, previous: str | None, changes: list[tuple[datetime, str]]
) -> list[AvailabilitySegment]:
    """Turn status changes into back-to-back segments covering [start, end]."""
    segments: list[AvailabilitySegment] = []
    current: str = previous or "unknown"
    cursor = start
    # Sort by time only: Python's sort is stable, so equal times keep the order the
    # data plane returned them in (time, then event id).
    for at, status in sorted(changes, key=lambda change: change[0]):
        at = min(max(at, start), end)
        if status == current:
            continue
        if at > cursor:
            segments.append(AvailabilitySegment(start=cursor, end=at, status=current))  # type: ignore[arg-type]
        cursor, current = at, status
    if end > cursor:
        segments.append(AvailabilitySegment(start=cursor, end=end, status=current))  # type: ignore[arg-type]
    return segments


def apply_live_status(
    segments: list[AvailabilitySegment],
    *,
    live_status: str | None,
    last_seen_at: datetime | None,
    start: datetime,
    end: datetime,
) -> list[AvailabilitySegment]:
    """Align only the open end with live connectivity, from last_seen onward.

    Recorded intervals stay as status events wrote them. Without last_seen, leave
    history alone (including an all-unknown window).
    """
    if not segments or live_status not in ("online", "offline") or last_seen_at is None:
        return segments
    seen = last_seen_at if last_seen_at.tzinfo else last_seen_at.replace(tzinfo=UTC)
    at = min(max(seen, start), end)
    last = segments[-1]
    if last.status == live_status or at <= last.start or at >= last.end:
        return segments
    return [
        *segments[:-1],
        AvailabilitySegment(start=last.start, end=at, status=last.status),
        AvailabilitySegment(start=at, end=last.end, status=live_status),  # type: ignore[arg-type]
    ]


def device_availability(
    data_plane: DataPlaneApi | None,
    *,
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    range_: TelemetryRange,
    now: datetime,
    live_status: str | None = None,
    last_seen_at: datetime | None = None,
) -> DeviceAvailabilityResponse:
    dp = require_data_plane(data_plane)
    window, _ = RANGES[range_]
    end = now.replace(microsecond=0)
    start = end - window
    params = {
        "stream": STATUS_STREAM,
        "device_id": str(device_id),
        "start": _iso(start),
        "end": _iso(end),
        "order": "asc",
        "limit": str(STATUS_PAGE_SIZE),
        "previous": "1",
    }
    body = dp.list_events(organization_id=organization_id, params=params)
    previous = _status_of(body["previous"]) if body.get("previous") else None
    raw = list(body.get("events", []))
    # Follow the cursor so a busy window is read to its end (bounded: a device
    # that flaps this often is shown as such well before the cap).
    pages = 1
    while body.get("next_cursor") and pages < STATUS_MAX_PAGES:
        body = dp.list_events(
            organization_id=organization_id,
            params={**params, "cursor": body["next_cursor"], "previous": "0"},
        )
        raw.extend(body.get("events", []))
        pages += 1
    changes: list[tuple[datetime, str]] = []
    for event in raw:
        status, at = _status_of(event), _parse_time(event.get("time"))
        if status and at:
            changes.append((at, status))
    changes = debounce_status_changes(changes)
    segments = apply_live_status(
        build_segments(start, end, previous, changes),
        live_status=live_status,
        last_seen_at=last_seen_at,
        start=start,
        end=end,
    )
    known = sum((s.end - s.start).total_seconds() for s in segments if s.status != "unknown")
    online = sum((s.end - s.start).total_seconds() for s in segments if s.status == "online")
    # Count transitions into offline, not offline messages: a repeated offline
    # (last will after a stop message) is the same outage.
    offline_count = 0
    state = previous
    for _, status in changes:
        if status == "offline" and state != "offline":
            offline_count += 1
        state = status
    return DeviceAvailabilityResponse(
        device_id=device_id,
        range=range_,
        start=start,
        end=end,
        uptime_percent=round(online * 100 / known, 2) if known > 0 else None,
        offline_count=offline_count,
        segments=segments,
        changes=[StatusChange(t=at, status=status) for at, status in changes][-200:],  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ deployments


class DeploymentHistoryItem(BaseModel):
    t: datetime
    status: str
    previous_status: str | None = None
    deployment_id: uuid.UUID | None = None
    deployment_name: str | None = None
    release_id: uuid.UUID | None = None
    release_name: str | None = None
    release_version: str | None = None
    seconds: float | None = None
    error: str | None = None


class DeviceDeploymentHistoryResponse(BaseModel):
    device_id: uuid.UUID
    items: list[DeploymentHistoryItem]


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def device_deployment_history(
    data_plane: DataPlaneApi | None,
    session: Session,
    *,
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    limit: int = 50,
) -> DeviceDeploymentHistoryResponse:
    dp = require_data_plane(data_plane)
    body = dp.list_events(
        organization_id=organization_id,
        params={"stream": DEPLOYMENTS_STREAM, "device_id": str(device_id), "order": "desc", "limit": str(limit)},
    )
    raw = body.get("events", [])
    deployment_ids = {d for d in (_uuid((e.get("data") or {}).get("deployment_id")) for e in raw) if d}
    release_ids = {r for r in (_uuid((e.get("data") or {}).get("release_id")) for e in raw) if r}
    deployments = (
        {
            row.id: row
            for row in session.scalars(
                select(Deployment).where(
                    Deployment.id.in_(deployment_ids), Deployment.organization_id == organization_id
                )
            )
        }
        if deployment_ids
        else {}
    )
    releases = (
        {
            row.id: row
            for row in session.scalars(
                select(Release).where(Release.id.in_(release_ids), Release.organization_id == organization_id)
            )
        }
        if release_ids
        else {}
    )
    items: list[DeploymentHistoryItem] = []
    for event in raw:
        data = event.get("data") or {}
        at = _parse_time(event.get("time"))
        if at is None:
            continue
        dep_id, rel_id = _uuid(data.get("deployment_id")), _uuid(data.get("release_id"))
        deployment = deployments.get(dep_id) if dep_id else None
        release = releases.get(rel_id) if rel_id else None
        seconds = data.get("seconds")
        items.append(
            DeploymentHistoryItem(
                t=at,
                status=str(data.get("status") or str(event.get("type", "")).rsplit(".", 1)[-1]),
                previous_status=data.get("previous_status"),
                deployment_id=dep_id,
                deployment_name=deployment.name if deployment else None,
                release_id=rel_id,
                release_name=release.name if release else None,
                release_version=release.version if release else None,
                seconds=float(seconds) if isinstance(seconds, (int, float)) else None,
                error=data.get("error"),
            )
        )
    return DeviceDeploymentHistoryResponse(device_id=device_id, items=items)


# ------------------------------------------------------------------ apps


class AppReading(BaseModel):
    name: str
    cpu_percent: float | None = None
    memory_bytes: float | None = None
    restarts: float | None = None
    healthy: bool | None = None
    health_ms: float | None = None
    running: bool | None = None


class AppEvent(BaseModel):
    t: datetime
    type: str
    app: str | None = None
    exit_code: int | None = None
    oom_killed: bool | None = None
    detail: str | None = None


class DeviceAppsResponse(BaseModel):
    device_id: uuid.UUID
    reported_at: datetime | None
    apps: list[AppReading]
    events: list[AppEvent]


# Agent metric keys: app.<name>.<field> (numeric, so ClickHouse keeps their history too).
_APP_FIELDS = {"cpu": "cpu_percent", "mem": "memory_bytes", "restarts": "restarts", "health_ms": "health_ms"}


def parse_app_readings(data: dict[str, Any]) -> list[AppReading]:
    apps: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if not key.startswith("app.") or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        name, _, field = key[4:].rpartition(".")
        if not name:
            continue
        entry = apps.setdefault(name, {"name": name})
        if field in _APP_FIELDS:
            entry[_APP_FIELDS[field]] = value
        elif field == "healthy":
            entry["healthy"] = value >= 1
        elif field == "up":
            entry["running"] = value >= 1
    return [AppReading.model_validate(entry) for _, entry in sorted(apps.items())]


def device_apps(
    data_plane: DataPlaneApi | None,
    *,
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    now: datetime,
) -> DeviceAppsResponse:
    dp = require_data_plane(data_plane)
    latest = dp.list_events(
        organization_id=organization_id,
        params={"stream": METRICS_STREAM, "device_id": str(device_id), "order": "desc", "limit": "1"},
    ).get("events", [])
    readings: list[AppReading] = []
    reported_at = None
    if latest:
        readings = parse_app_readings(latest[0].get("data") or {})
        reported_at = _parse_time(latest[0].get("time"))
    raw = dp.list_events(
        organization_id=organization_id,
        params={
            "stream": EVENTS_STREAM,
            "device_id": str(device_id),
            "start": _iso(now - timedelta(days=7)),
            "order": "desc",
            "limit": "200",
        },
    ).get("events", [])
    events: list[AppEvent] = []
    for event in raw:
        kind = str(event.get("type", ""))
        at = _parse_time(event.get("time"))
        if not kind.startswith("app.") or at is None:
            continue
        data = event.get("data") or {}
        exit_code = data.get("exit_code")
        events.append(
            AppEvent(
                t=at,
                type=kind,
                app=data.get("app") if isinstance(data.get("app"), str) else None,
                exit_code=exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None,
                oom_killed=data.get("oom_killed") if isinstance(data.get("oom_killed"), bool) else None,
                detail=str(data["detail"])[:300] if data.get("detail") else None,
            )
        )
        if len(events) >= 50:
            break
    return DeviceAppsResponse(device_id=device_id, reported_at=reported_at, apps=readings, events=events)


# ------------------------------------------------------------------ log history


class LogEntry(BaseModel):
    t: datetime
    message: str
    priority: int | None = None
    unit: str | None = None
    source: str | None = None


class DeviceLogHistoryResponse(BaseModel):
    device_id: uuid.UUID
    range: TelemetryRange
    query: str | None
    entries: list[LogEntry]
    next_cursor: str | None


def device_log_history(
    data_plane: DataPlaneApi | None,
    *,
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    range_: TelemetryRange,
    query: str | None,
    cursor: str | None,
    limit: int,
    now: datetime,
) -> DeviceLogHistoryResponse:
    dp = require_data_plane(data_plane)
    window, _ = RANGES[range_]
    params = {
        "stream": LOGS_STREAM,
        "device_id": str(device_id),
        "start": _iso(now - window),
        "end": _iso(now),
        "order": "desc",
        "limit": str(limit),
    }
    if query:
        params["q"] = query
    if cursor:
        params["cursor"] = cursor
    body = dp.list_events(organization_id=organization_id, params=params)
    entries: list[LogEntry] = []
    for event in body.get("events", []):
        data = event.get("data") or {}
        at = _parse_time(event.get("time"))
        if at is None or not isinstance(data.get("message"), str):
            continue
        priority = data.get("priority")
        entries.append(
            LogEntry(
                t=at,
                message=data["message"],
                priority=priority if isinstance(priority, int) and not isinstance(priority, bool) else None,
                unit=data.get("unit") if isinstance(data.get("unit"), str) else None,
                source=data.get("source") if isinstance(data.get("source"), str) else None,
            )
        )
    return DeviceLogHistoryResponse(
        device_id=device_id, range=range_, query=query, entries=entries, next_cursor=body.get("next_cursor")
    )
