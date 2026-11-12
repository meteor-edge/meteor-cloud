"""Record every refused (403) organization request as an ``access.denied`` audit row.

Services that already audit their own refusals (browser SSH, device logs) mark the request,
and no second row is written. The row goes through its own session: the request's session
may hold half-done work that must not be committed.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.requests import Request

from app.core.audit_context import current_request_context
from app.core.exceptions import AppError

logger = structlog.get_logger(__name__)


def record_forbidden(request: Request, exc: AppError) -> None:
    context = current_request_context()
    if context is None or context.denial_recorded or context.actor_type is None:
        return
    raw_org = request.path_params.get("organization_id")
    if raw_org is None:
        return
    try:
        organization_id = uuid.UUID(str(raw_org))
    except ValueError:
        return
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path

    from app.audit.service import OUTCOME_DENIED, AuditRecorder
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        AuditRecorder(session).record(
            action="access.denied",
            resource_type="route",
            resource_id=None,
            organization_id=organization_id,
            outcome=OUTCOME_DENIED,
            metadata={"method": request.method, "path": path, "code": exc.code},
        )
        session.commit()
    except Exception:  # an audit failure must never turn a 403 into a 500
        session.rollback()
        logger.warning("audit_denial_not_recorded", path=path, exc_info=True)
    finally:
        session.close()
