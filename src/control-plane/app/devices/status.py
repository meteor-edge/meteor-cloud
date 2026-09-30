"""Device connectivity status calculation.

Online/offline is derived from ``last_seen_at`` and a configurable threshold so
the same rule is applied everywhere (list, detail, and filtering).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime, timedelta


class ConnectivityStatus(enum.StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    NEVER_SEEN = "never_seen"


def connectivity_status(
    last_seen_at: datetime | None,
    *,
    offline_threshold_seconds: int,
    now: datetime | None = None,
) -> ConnectivityStatus:
    """Return the connectivity status for a device."""
    if last_seen_at is None:
        return ConnectivityStatus.NEVER_SEEN

    current = now or datetime.now(UTC)
    # Tolerate naive datetimes coming from the database by assuming UTC.
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)

    threshold = timedelta(seconds=offline_threshold_seconds)
    if current - last_seen_at <= threshold:
        return ConnectivityStatus.ONLINE
    return ConnectivityStatus.OFFLINE


def offline_cutoff(
    *,
    offline_threshold_seconds: int,
    now: datetime | None = None,
) -> datetime:
    """Return the timestamp before which a device is considered offline."""
    current = now or datetime.now(UTC)
    return current - timedelta(seconds=offline_threshold_seconds)
