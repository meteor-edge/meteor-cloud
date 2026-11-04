"""Fleet-wide gauges computed when metrics are scraped.

Global totals only (never per organization or device), from a few COUNT ... GROUP BY
queries, cached in-process so frequent scrapes stay cheap. There is no background task:
nothing runs unless something reads /metrics. If the database is unreachable the
fleet gauges are absent from that scrape, ``meteorcloud_database_up`` reads 0, and the
scrape itself still succeeds.

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

from app.core.config import Settings
from app.core.metrics import register_gauge_callback
from app.devices.models import Device
from app.devices.status import offline_cutoff
from app.ports.observability import GaugeSample

logger = logging.getLogger(__name__)

WAITING_DEVICE_STATUSES = ("pending", "queued", "updating")
# A paused Deploy (by an operator or by its failure threshold) still holds devices.
RUNNING_DEPLOYMENT_STATUSES = ("in_progress", "paused")


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
                self._cached = {**self._query(), "meteorcloud_database_up": [({}, 1)]}
            except Exception:
                logger.warning("fleet metrics query failed", exc_info=True)
                self._cached = {"meteorcloud_database_up": [({}, 0)]}
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
            running = dict(
                session.execute(
                    select(Deployment.status, func.count())
                    .where(Deployment.status.in_(RUNNING_DEPLOYMENT_STATUSES))
                    .group_by(Deployment.status)
                ).all()
            )
            waiting_rows = dict(
                session.execute(
                    select(DeploymentDevice.status, func.count())
                    .join(Deployment, Deployment.id == DeploymentDevice.deployment_id)
                    .where(Deployment.status == "in_progress")
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
            "meteorcloud_deployments": [
                ({"status": status}, running.get(status, 0)) for status in RUNNING_DEPLOYMENT_STATUSES
            ],
            "meteorcloud_deployment_devices": [
                ({"status": status}, waiting_rows.get(status, 0)) for status in WAITING_DEVICE_STATUSES
            ],
        }

    def register(self, *, enabled: bool = True) -> None:
        """Register the gauges; ``enabled=False`` registers empty ones.

        prometheus_client keeps one process-wide registry, so a callback registered by an
        earlier app would otherwise keep querying the database for a later app that turned
        fleet metrics off.
        """
        gauges = {
            "meteorcloud_devices": ("Devices by connectivity state", ["state"]),
            "meteorcloud_devices_disabled": ("Devices switched off by an operator", []),
            "meteorcloud_devices_software_unhealthy": ("Devices with at least one unhealthy workload", []),
            "meteorcloud_deployments": ("Deploys currently running or paused", ["status"]),
            "meteorcloud_database_up": ("1 when the last fleet query reached the database, else 0", []),
            "meteorcloud_deployment_devices": ("Devices waiting on a running Deploy", ["status"]),
        }
        for name, (documentation, labelnames) in gauges.items():
            if enabled:
                callback = lambda name=name: self.snapshot().get(name, [])  # noqa: E731
            else:
                callback = list
            register_gauge_callback(name, documentation, labelnames, callback)


def register_fleet_metrics(settings: Settings) -> None:
    """Register fleet gauges (empty when FLEET_METRICS_ENABLED=false; no-op when metrics are off)."""
    from app.core.database import SessionLocal

    FleetMetrics(
        SessionLocal,
        offline_threshold_seconds=settings.device_offline_threshold_seconds,
        cache_seconds=settings.fleet_metrics_cache_seconds,
    ).register(enabled=settings.fleet_metrics_enabled)
