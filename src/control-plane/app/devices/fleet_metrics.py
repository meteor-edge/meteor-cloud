"""Fleet-wide gauges computed when metrics are scraped.

Global totals only (never per organization or device), from a few COUNT ... GROUP BY
queries, cached in-process so frequent scrapes stay cheap. There is no background task:
nothing runs unless something reads /metrics. If the database is unreachable the
gauges are simply absent from that scrape; the scrape itself still succeeds.

With several API replicas every replica reports the same totals, so queries must
aggregate these gauges with ``max``, not ``sum``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.metrics import register_gauge_callback
from app.devices.models import Device
from app.devices.status import offline_cutoff
from app.ports.observability import GaugeSample

logger = logging.getLogger(__name__)

WAITING_DEVICE_STATUSES = ("pending", "queued", "updating")


class FleetMetrics:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        offline_threshold_seconds: int,
        cache_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._offline_threshold_seconds = offline_threshold_seconds
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._cached_at: float | None = None
        self._cached: dict[str, list[GaugeSample]] = {}

    def snapshot(self) -> dict[str, list[GaugeSample]]:
        """Return all fleet gauges, querying at most once per cache window."""
        with self._lock:
            now = self._clock()
            if self._cached_at is not None and now - self._cached_at < self._cache_seconds:
                return self._cached
            try:
                self._cached = self._query()
            except Exception:
                logger.warning("fleet metrics query failed", exc_info=True)
                self._cached = {}
            self._cached_at = now
            return self._cached

    def _query(self) -> dict[str, list[GaugeSample]]:
        from app.deployments.models import Deployment, DeploymentDevice
        from app.deployments.software_models import DeviceSoftware

        cutoff = offline_cutoff(offline_threshold_seconds=self._offline_threshold_seconds, now=datetime.now(UTC))
        with self._session_factory() as session:
            never_seen = session.scalar(select(func.count()).where(Device.last_seen_at.is_(None))) or 0
            online = session.scalar(select(func.count()).where(Device.last_seen_at >= cutoff)) or 0
            offline = session.scalar(select(func.count()).where(Device.last_seen_at < cutoff)) or 0
            disabled = session.scalar(select(func.count()).where(Device.is_enabled.is_(False))) or 0
            unhealthy = (
                session.scalar(
                    select(func.count(func.distinct(DeviceSoftware.device_id))).where(
                        DeviceSoftware.health == "unhealthy"
                    )
                )
                or 0
            )
            in_progress = (
                session.scalar(select(func.count()).select_from(Deployment).where(Deployment.status == "in_progress"))
                or 0
            )
            waiting_rows = dict(
                session.execute(
                    select(DeploymentDevice.status, func.count())
                    .where(DeploymentDevice.status.in_(WAITING_DEVICE_STATUSES))
                    .group_by(DeploymentDevice.status)
                ).all()
            )
        return {
            "meteorcloud_devices": [
                ({"state": "online"}, online),
                ({"state": "offline"}, offline),
                ({"state": "never_seen"}, never_seen),
            ],
            "meteorcloud_devices_disabled": [({}, disabled)],
            "meteorcloud_devices_software_unhealthy": [({}, unhealthy)],
            "meteorcloud_deployments": [({"status": "in_progress"}, in_progress)],
            "meteorcloud_deployment_devices": [
                ({"status": status}, waiting_rows.get(status, 0)) for status in WAITING_DEVICE_STATUSES
            ],
        }

    def register(self) -> None:
        gauges = {
            "meteorcloud_devices": ("Devices by connectivity state", ["state"]),
            "meteorcloud_devices_disabled": ("Devices switched off by an operator", []),
            "meteorcloud_devices_software_unhealthy": ("Devices with at least one unhealthy workload", []),
            "meteorcloud_deployments": ("Deploys currently running", ["status"]),
            "meteorcloud_deployment_devices": ("Devices waiting on a running Deploy", ["status"]),
        }
        for name, (documentation, labelnames) in gauges.items():
            register_gauge_callback(name, documentation, labelnames, lambda name=name: self.snapshot().get(name, []))


def register_fleet_metrics(settings) -> None:
    """Register fleet gauges with the active metrics backend (no-op when metrics are off)."""
    from app.core.database import SessionLocal

    FleetMetrics(
        SessionLocal,
        offline_threshold_seconds=settings.device_offline_threshold_seconds,
        cache_seconds=settings.fleet_metrics_cache_seconds,
    ).register()
