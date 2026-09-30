"""HTTP routes for organization audit events."""

from __future__ import annotations

import uuid
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
) -> list[AuditEventResponse]:
    return service.list_for_organization(
        actor=current_user,
        organization_id=organization_id,
        limit=limit,
    )
