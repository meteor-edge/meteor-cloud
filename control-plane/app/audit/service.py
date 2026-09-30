"""Write and list append-only audit events."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.schemas import AuditEventResponse
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.request_id import current_request_id
from app.identity.models import User
from app.tenancy.models import OrganizationRole
from app.tenancy.repository import OrganizationRepository

logger = structlog.get_logger(__name__)


class AuditRecorder:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)

    def record(
        self,
        *,
        action: str,
        resource_type: str,
        actor: User | None = None,
        organization_id: uuid.UUID | None = None,
        resource_id: uuid.UUID | str | None = None,
        outcome: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            organization_id=organization_id,
            actor_user_id=actor.id if actor is not None else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            request_id=current_request_id(),
            outcome=outcome,
            metadata_=metadata or {},
        )
        self.session.add(event)
        logger.info(
            "audit",
            action=action,
            resource_type=resource_type,
            resource_id=event.resource_id,
            organization_id=str(organization_id) if organization_id else None,
            outcome=outcome,
        )
        return event

    def list_for_organization(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        limit: int = 100,
    ) -> list[AuditEventResponse]:
        result = self.organizations.get_for_user(
            organization_id=organization_id,
            user_id=actor.id,
        )
        if result is None:
            raise NotFoundError("organization_not_found", "Organization not found.")
        membership = result[1]
        if membership.role not in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
            raise ForbiddenError(
                "insufficient_permissions",
                "Only owners and admins can view audit events.",
            )
        statement = (
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
        rows = list(self.session.scalars(statement))
        return [AuditEventResponse.model_validate(row) for row in rows]
