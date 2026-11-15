"""Write an outbox event for every change of a device's status in a Deploy.

One ``before_flush`` hook sees every change, wherever in the code the status is
set (heartbeat reports, the sweeper, cancel, rollback, timeouts), and adds the
outbox row to the same transaction: the event exists exactly when the change
was committed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.history.models import PlatformEventOutbox

DEPLOYMENTS_STREAM = "meteor.deployments"
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "skipped", "timed_out"})


def history_enabled() -> bool:
    """Only record when there is a data plane to send to; otherwise the outbox would only grow."""
    settings = get_settings()
    return bool(settings.data_plane_api_url and settings.data_plane_internal_token)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _deployment_event(
    row: Any, before: str | None, now: datetime, *, organization_id: uuid.UUID, release_id: uuid.UUID
) -> PlatformEventOutbox:
    activated = _as_utc(row.activated_at)
    data: dict[str, Any] = {
        "deployment_id": str(row.deployment_id),
        "release_id": str(release_id),
        "status": row.status,
        "previous_status": before,
    }
    if row.last_error and row.status in {"failed", "timed_out"}:
        data["error"] = str(row.last_error)[:500]
    if activated is not None:
        data["activated_at"] = activated.isoformat()
        if row.status in TERMINAL:
            data["seconds"] = round(max(0.0, (now - activated).total_seconds()), 1)
    return PlatformEventOutbox(
        id=uuid.uuid4(),
        organization_id=organization_id,
        device_id=row.device_id,
        stream=DEPLOYMENTS_STREAM,
        type=f"deployment.device.{row.status}",
        occurred_at=now,
        data=data,
    )


def _before_flush(session: Session, _context: object, _instances: object) -> None:
    from app.deployments.models import Deployment, DeploymentDevice

    candidates = [obj for obj in (*session.new, *session.dirty) if isinstance(obj, DeploymentDevice)]
    if not candidates or not history_enabled():
        return
    now = datetime.now(UTC)
    # A Deploy created in this flush is not in the database yet: find it among the new objects.
    pending = {obj.id: obj for obj in session.new if isinstance(obj, Deployment)}
    with session.no_autoflush:
        for row in candidates:
            state = inspect(row)
            history = state.attrs.status.history
            if state.pending:
                before = None
            elif history.has_changes():
                before = history.deleted[0] if history.deleted else None
            else:
                continue
            if row.status is None or before == row.status:
                continue
            deployment = pending.get(row.deployment_id) or session.get(Deployment, row.deployment_id)
            if deployment is None:
                continue
            session.add(
                _deployment_event(
                    row, before, now, organization_id=deployment.organization_id, release_id=deployment.release_id
                )
            )


def install() -> None:
    """Register the hook once (idempotent)."""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
