"""HTTP routes for organization audit events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.audit.schemas import AuditEventResponse
from app.audit.service import AuditRecorder
from app.core.database import get_db
from app.identity.dependencies import CurrentUser

router = APIRouter(prefix="/api/v1/organizations/{organization_id}", tags=["audit"])


def get_audit_recorder(session: Annotated[Session, Depends(get_db)]) -> AuditRecorder:
    return AuditRecorder(session)


AuditSvc = Annotated[AuditRecorder, Depends(get_audit_recorder)]


@router.get("/audit-events", response_model=list[AuditEventResponse])
def list_audit_events(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: AuditSvc,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    action: Annotated[
        str | None, Query(max_length=80, description="Exact action, or a prefix ending in '.' or '*'.")
    ] = None,
    resource_type: Annotated[str | None, Query(max_length=80)] = None,
    resource_id: Annotated[str | None, Query(max_length=64)] = None,
    actor_user_id: uuid.UUID | None = None,
    actor_type: Annotated[str | None, Query(max_length=32)] = None,
    outcome: Annotated[str | None, Query(max_length=32)] = None,
    since: datetime | None = None,
    before: Annotated[datetime | None, Query(description="Page: created_at of the last row already shown.")] = None,
    before_id: Annotated[uuid.UUID | None, Query(description="Page: id of the last row already shown.")] = None,
) -> list[AuditEventResponse]:
    return service.list_for_organization(
        actor=current_user,
        organization_id=organization_id,
        limit=limit,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        outcome=outcome,
        since=since,
        before=before,
        before_id=before_id,
    )
