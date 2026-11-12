"""Write and list append-only audit events.

``AuditRecorder.record`` is the one way any control-plane module writes an audit row.
It adds the row to the caller's session (the caller commits it with its own change, so
the row and the change land together or not at all) and fills request id, client IP,
user agent and the authenticated actor from the current HTTP request on its own.

Action names are ``<resource>.<verb>`` in snake_case (``device.delete``,
``enrollment_key.revoke``); refusals end in ``.denied`` or pass ``outcome="denied"``,
failed attempts pass ``outcome="failure"``. Never put secrets (tokens, keys,
passwords, claim secrets) in ``metadata``: a prefix or fingerprint is enough.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.schemas import AuditEventResponse
from app.core.audit_context import current_request_context
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.request_id import current_request_id
from app.identity.models import User
from app.tenancy.models import OrganizationRole
from app.tenancy.repository import OrganizationRepository

logger = structlog.get_logger(__name__)

OUTCOME_SUCCESS = "success"
OUTCOME_DENIED = "denied"
OUTCOME_FAILURE = "failure"


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
        outcome: str = OUTCOME_SUCCESS,
        metadata: dict[str, Any] | None = None,
        actor_type: str | None = None,
        actor_id: uuid.UUID | str | None = None,
    ) -> AuditEvent:
        """Add one audit row to the session. The caller commits.

        The actor is, in order: ``actor`` (a user), an explicit ``actor_type``/``actor_id``
        (a device, an application...), whoever authenticated the current request, or
        ``system`` when there is no request (CLI, background sweeper).
        """
        context = current_request_context()
        if actor is not None:
            resolved_type, resolved_id = "user", str(actor.id)
        elif actor_type is not None:
            if actor_type == "user":
                raise ValueError("Pass a User through actor= instead of actor_type='user'.")
            resolved_type, resolved_id = actor_type, str(actor_id) if actor_id is not None else None
        elif context is not None and context.actor_type is not None:
            resolved_type, resolved_id = context.actor_type, context.actor_id
        else:
            resolved_type, resolved_id = "system", None
        actor_user_id = actor.id if actor is not None else None
        if actor_user_id is None and resolved_type == "user" and context is not None:
            actor_user_id = context.actor_user_id
        event = AuditEvent(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            actor_type=resolved_type,
            actor_id=resolved_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            request_id=current_request_id(),
            ip=context.ip if context is not None else None,
            user_agent=context.user_agent if context is not None else None,
            outcome=outcome,
            metadata_=metadata or {},
        )
        self.session.add(event)
        if context is not None and (outcome != OUTCOME_SUCCESS or action.endswith(".denied")):
            context.denial_recorded = True
        logger.info(
            "audit",
            action=action,
            resource_type=resource_type,
            resource_id=event.resource_id,
            organization_id=str(organization_id) if organization_id else None,
            actor_type=resolved_type,
            actor_id=resolved_id,
            outcome=outcome,
        )
        return event

    def list_for_organization(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        limit: int = 100,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_type: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        before: datetime | None = None,
        before_id: uuid.UUID | None = None,
    ) -> list[AuditEventResponse]:
        """Newest first. ``action`` ending in ``.`` or ``*`` matches a prefix (``device.``).

        Page with ``before`` and ``before_id`` = ``created_at`` and ``id`` of the last row of
        the previous page (rows written in one transaction share ``created_at``).
        """
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
        statement = select(AuditEvent, User.email).outerjoin(User, User.id == AuditEvent.actor_user_id)
        statement = statement.where(AuditEvent.organization_id == organization_id)
        if action:
            if action.endswith("*") or action.endswith("."):
                prefix = action.rstrip("*")
                escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                statement = statement.where(AuditEvent.action.like(f"{escaped}%", escape="\\"))
            else:
                statement = statement.where(AuditEvent.action == action)
        if resource_type:
            statement = statement.where(AuditEvent.resource_type == resource_type)
        if resource_id:
            statement = statement.where(AuditEvent.resource_id == resource_id)
        if actor_user_id is not None:
            statement = statement.where(AuditEvent.actor_user_id == actor_user_id)
        if actor_type:
            statement = statement.where(AuditEvent.actor_type == actor_type)
        if outcome:
            statement = statement.where(AuditEvent.outcome == outcome)
        if since is not None:
            statement = statement.where(AuditEvent.created_at >= since)
        if before is not None and before_id is not None:
            statement = statement.where(tuple_(AuditEvent.created_at, AuditEvent.id) < tuple_(before, before_id))
        elif before is not None:
            statement = statement.where(AuditEvent.created_at < before)
        statement = statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
        rows = self.session.execute(statement).all()
        return [
            AuditEventResponse.model_validate(event).model_copy(update={"actor_email": email}) for event, email in rows
        ]
