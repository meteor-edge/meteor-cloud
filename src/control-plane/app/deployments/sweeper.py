"""Periodic Deploy tick, so rollouts advance without waiting for a heartbeat.

Batched delays, phased soak times and pending timeouts are otherwise only
evaluated when a device in the Deploy reports. Each API replica runs the loop;
a session-level advisory lock, held on its own connection for the whole sweep,
lets one replica sweep at a time. Each Deploy is locked with
``FOR UPDATE SKIP LOCKED`` (skipping one a heartbeat is ticking right now) and
committed on its own, so its row lock is released before the next Deploy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.deployments.models import Deployment, DeploymentStatus

logger = logging.getLogger(__name__)

# Fixed pg advisory lock key for the Deploy sweeper ("deploy" in ASCII).
SWEEP_LOCK_KEY = 0x6465706C6F79


def sweep_deployments(session: Session) -> int:
    """Tick every in-progress batched/phased native Deploy once. Returns how many were ticked."""
    from app.deployments.service import DeploymentService

    deployment_ids = session.scalars(
        select(Deployment.id)
        .where(
            Deployment.status == DeploymentStatus.IN_PROGRESS.value,
            Deployment.provider == "native",
            Deployment.rollout["strategy"].astext.in_(["batched", "phased"]),
        )
        .order_by(Deployment.id)
    ).all()
    session.rollback()
    service = DeploymentService(session)
    ticked = 0
    for deployment_id in deployment_ids:
        try:
            deployment = session.scalar(
                select(Deployment)
                .where(
                    Deployment.id == deployment_id,
                    Deployment.status == DeploymentStatus.IN_PROGRESS.value,
                )
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if deployment is None:
                session.rollback()
                continue
            service.tick_deployment(deployment)
            session.commit()
            ticked += 1
        except Exception:
            session.rollback()
            logger.exception("Deploy tick failed for %s", deployment_id)
    return ticked


def sweep_once(engine: Engine, session_factory: Callable[[], Session]) -> int:
    """Sweep if no other replica is sweeping; 0 when the lock is taken."""
    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_LOCK_KEY}
        )
        # End the transaction; the session-level lock stays with the connection.
        lock_connection.commit()
        if not acquired:
            return 0
        try:
            with session_factory() as session:
                return sweep_deployments(session)
        finally:
            try:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY}
                )
                lock_connection.commit()
            except Exception:
                # Closing the database session drops the lock if unlock failed.
                logger.exception("Deploy sweep unlock failed")
                lock_connection.invalidate()


def _sweep_with_new_session() -> int:
    from app.core.database import SessionLocal, engine

    return sweep_once(engine, SessionLocal)


async def run_sweeper(interval_seconds: int) -> None:
    """Sweep every ``interval_seconds`` in a worker thread; errors never stop the loop."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(_sweep_with_new_session)
        except Exception:
            logger.exception("Deploy sweep failed")
