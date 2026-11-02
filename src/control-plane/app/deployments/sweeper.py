"""Periodic Deploy tick, so rollouts advance without waiting for a heartbeat.

Batched delays, phased soak times and pending timeouts are otherwise only
evaluated when a device in the Deploy reports. Each API replica runs the loop;
a transaction-level advisory lock lets one replica sweep at a time, and
``FOR UPDATE SKIP LOCKED`` skips Deploys a heartbeat is ticking right now.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.deployments.models import Deployment, DeploymentStatus

logger = logging.getLogger(__name__)

# Fixed pg advisory lock key for the Deploy sweeper ("deploy" in ASCII).
SWEEP_LOCK_KEY = 0x6465706C6F79


def sweep_deployments(session: Session) -> int:
    """Tick every in-progress batched/phased native Deploy once. Returns how many were ticked."""
    from app.deployments.service import DeploymentService

    # xact lock: released by the commit/rollback below even if the pool swaps connections.
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SWEEP_LOCK_KEY}):
        session.rollback()
        return 0
    deployments = session.scalars(
        select(Deployment)
        .where(
            Deployment.status == DeploymentStatus.IN_PROGRESS.value,
            Deployment.provider == "native",
            Deployment.rollout["strategy"].astext.in_(["batched", "phased"]),
        )
        .order_by(Deployment.id)
        .with_for_update(skip_locked=True)
    ).all()
    service = DeploymentService(session)
    ticked = 0
    for deployment in deployments:
        try:
            with session.begin_nested():
                service.tick_deployment(deployment)
            ticked += 1
        except Exception:
            logger.exception("Deploy tick failed for %s", deployment.id)
    session.commit()
    return ticked


def _sweep_with_new_session() -> int:
    from app.core.database import SessionLocal

    with SessionLocal() as session:
        try:
            return sweep_deployments(session)
        except Exception:
            session.rollback()
            raise


async def run_sweeper(interval_seconds: int) -> None:
    """Sweep every ``interval_seconds`` in a worker thread; errors never stop the loop."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(_sweep_with_new_session)
        except Exception:
            logger.exception("Deploy sweep failed")
