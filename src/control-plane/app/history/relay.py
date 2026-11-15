"""Send the platform event outbox to the data plane.

Each API replica runs the loop; a session-level advisory lock lets one replica
send at a time. Rows are deleted only after the data plane answered, and the
row id is the event id there, so a batch sent twice (crash between the answer
and the commit) is stored once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from app.history.models import PlatformEventOutbox
from app.ports.data_plane import DataPlaneApi

logger = logging.getLogger(__name__)

# Fixed pg advisory lock key for the relay ("history" in ASCII).
RELAY_LOCK_KEY = 0x686973746F7279
BATCH_SIZE = 500
# While the data plane is down the outbox grows; older events are dropped so it stays bounded.
MAX_AGE = timedelta(days=7)
# Batches per pass: a large backlog is drained over several passes, so a pass
# (a worker thread that shutdown cannot cancel) always ends within seconds.
MAX_BATCHES_PER_PASS = 10


def relay_batch(session: Session, data_plane: DataPlaneApi, *, now: datetime | None = None) -> int:
    """Send up to BATCH_SIZE events; returns how many were sent."""
    now = now or datetime.now(UTC)
    dropped = session.execute(
        delete(PlatformEventOutbox).where(PlatformEventOutbox.created_at < now - MAX_AGE)
    ).rowcount
    if dropped:
        logger.warning("Dropped %s platform events older than %s that the data plane never took", dropped, MAX_AGE)
        session.commit()
    rows = session.scalars(
        select(PlatformEventOutbox)
        .order_by(PlatformEventOutbox.created_at, PlatformEventOutbox.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    ).all()
    if not rows:
        session.rollback()
        return 0
    try:
        data_plane.write_events(
            [
                {
                    "id": str(row.id),
                    "organization_id": str(row.organization_id),
                    "device_id": str(row.device_id) if row.device_id else "",
                    "stream": row.stream,
                    "type": row.type,
                    "time": row.occurred_at.isoformat(),
                    "data": row.data,
                }
                for row in rows
            ]
        )
    except Exception:
        session.rollback()
        raise
    session.execute(delete(PlatformEventOutbox).where(PlatformEventOutbox.id.in_([row.id for row in rows])))
    session.commit()
    return len(rows)


def relay_once(engine: Engine, session_factory: Callable[[], Session], data_plane: DataPlaneApi) -> int:
    """Drain the outbox if no other replica is draining it; 0 when the lock is taken."""
    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": RELAY_LOCK_KEY})
        lock_connection.commit()
        if not acquired:
            return 0
        try:
            sent = 0
            with session_factory() as session:
                for _ in range(MAX_BATCHES_PER_PASS):
                    n = relay_batch(session, data_plane)
                    sent += n
                    if n < BATCH_SIZE:
                        break
                return sent
        finally:
            try:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RELAY_LOCK_KEY})
                lock_connection.commit()
            except Exception:
                logger.exception("History relay unlock failed")
                lock_connection.invalidate()


def _relay_with_new_session() -> int:
    from app.adapters.data_plane_api import DataPlaneHttpApi
    from app.core.config import get_settings
    from app.core.database import SessionLocal, engine

    return relay_once(engine, SessionLocal, DataPlaneHttpApi(get_settings()))


async def run_relay(interval_seconds: float) -> None:
    """Drain every ``interval_seconds``; backs off to a minute while the data plane fails."""
    delay = interval_seconds
    while True:
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(_relay_with_new_session)
            delay = interval_seconds
        except Exception as exc:
            delay = min(60.0, delay * 2)
            logger.warning("History relay failed (%s); retrying in %ss", type(exc).__name__, delay)
