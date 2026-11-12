"""One-off device actions from the console: restart, shut down, SSH user changes.

An administrator orders an action; the device collects it once over its authenticated
API, applies it and reports back. A password is encrypted at rest and erased as soon
as the device collects it (or when the action expires). It never appears in a response,
a log line or the audit trail. Collecting marks the action sent so a restart is never
handed out twice after the device comes back.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.secretbox import SecretBoxError, seal, unseal
from app.device_actions.models import (
    KIND_ADD_USER,
    KIND_REBOOT,
    KIND_SET_PASSWORD,
    KIND_SHUTDOWN,
    KIND_UPDATE_AGENT,
    POWER_KINDS,
    STATE_DONE,
    STATE_FAILED,
    STATE_PENDING,
    STATE_SENT,
    UPDATE_AGENT_KINDS,
    USER_KINDS,
    DeviceAction,
)
from app.device_actions.schemas import (
    AddUserRequest,
    AgentActionReportRequest,
    AgentActionReportResponse,
    AgentDeviceAction,
    AgentDeviceActionsResponse,
    DeviceActionResponse,
    DeviceActionsResponse,
    SetPasswordRequest,
)
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.identity.models import User

logger = logging.getLogger(__name__)

POWER_PERMISSION = "device.reboot"
USERS_PERMISSION = "device.users"
UPDATE_AGENT_PERMISSION = "device.update_agent"
_NOT_COLLECTED = "The device did not collect this action in time."
_UNREADABLE = "The stored password could not be read (the server's encryption key changed). Enter it again."
_MAX_PENDING = 5


def _context(row: DeviceAction) -> str:
    return f"device-action:{row.device_id}:{row.id}"


class DeviceActionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.devices = DeviceRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def _require_device(self, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, permission: str) -> Device:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, permission, device_group_id=device.device_group_id)
        return device

    def _pending(self, device_id: uuid.UUID, *, for_update: bool = False) -> list[DeviceAction]:
        statement = (
            select(DeviceAction)
            .where(DeviceAction.device_id == device_id, DeviceAction.state == STATE_PENDING)
            .order_by(DeviceAction.created_at)
        )
        if for_update:
            statement = statement.with_for_update()
        return list(self.session.scalars(statement))

    def _expire(self, rows: list[DeviceAction]) -> bool:
        now = datetime.now(UTC)
        dirty = False
        for row in rows:
            if row.state == STATE_PENDING and row.expires_at <= now:
                row.password_sealed = None
                row.state = STATE_FAILED
                row.last_error = _NOT_COLLECTED
                row.reported_at = now
                dirty = True
        return dirty

    def _to_response(self, row: DeviceAction) -> DeviceActionResponse:
        return DeviceActionResponse.model_validate(row)

    def _view(self, actor: User, organization_id: uuid.UUID, device: Device) -> DeviceActionsResponse:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        can_power = self.authz.check(membership, POWER_PERMISSION, device_group_id=device.device_group_id)
        can_users = self.authz.check(membership, USERS_PERMISSION, device_group_id=device.device_group_id)
        can_update = self.authz.check(membership, UPDATE_AGENT_PERMISSION, device_group_id=device.device_group_id)
        statement = (
            select(DeviceAction)
            .where(DeviceAction.device_id == device.id)
            .order_by(DeviceAction.created_at.desc())
            .limit(20)
        )
        rows = list(self.session.scalars(statement))
        if self._expire([row for row in rows if row.state == STATE_PENDING]):
            self.session.commit()
        actions = [
            self._to_response(row)
            for row in rows
            if (row.kind in POWER_KINDS and can_power)
            or (row.kind in USER_KINDS and can_users)
            or (row.kind in UPDATE_AGENT_KINDS and can_update)
        ]
        return DeviceActionsResponse(actions=actions)

    def _in_flight_update(self, device_id: uuid.UUID) -> DeviceAction | None:
        return self.session.scalar(
            select(DeviceAction)
            .where(
                DeviceAction.device_id == device_id,
                DeviceAction.kind == KIND_UPDATE_AGENT,
                DeviceAction.state.in_((STATE_PENDING, STATE_SENT)),
            )
            .order_by(DeviceAction.created_at.desc())
            .limit(1)
        )

    def _enqueue(
        self,
        *,
        actor: User,
        device: Device,
        kind: str,
        username: str | None = None,
        password: str | None = None,
        sudo: bool = False,
        authorized_key: str | None = None,
        from_version: str | None = None,
        audit_action: str,
        metadata: dict,
    ) -> DeviceActionsResponse:
        pending = self._pending(device.id, for_update=True)
        self._expire(pending)
        pending = [row for row in pending if row.state == STATE_PENDING]
        if len(pending) >= _MAX_PENDING:
            raise ConflictError(
                "too_many_pending_actions",
                f"A device can have at most {_MAX_PENDING} waiting actions. Wait for it to collect them.",
            )
        if kind in POWER_KINDS and any(row.kind in POWER_KINDS for row in pending):
            raise ConflictError(
                "power_action_pending",
                "A restart or shut-down is already waiting for this device.",
            )
        if kind == KIND_UPDATE_AGENT and self._in_flight_update(device.id) is not None:
            raise ConflictError(
                "agent_update_pending",
                "An agent update is already waiting for this device or still running.",
            )
        row = DeviceAction(
            organization_id=device.organization_id,
            device_id=device.id,
            kind=kind,
            state=STATE_PENDING,
            from_version=from_version,
            username=username,
            sudo=sudo,
            authorized_key=authorized_key,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.device_action_ttl_seconds),
            requested_by_user_id=actor.id,
        )
        self.session.add(row)
        self.session.flush()
        if password is not None:
            row.password_sealed = seal(self.settings, password, context=_context(row))
        self.audit.record(
            actor=actor,
            organization_id=device.organization_id,
            action=audit_action,
            resource_type="device",
            resource_id=device.id,
            metadata=metadata,
        )
        self.session.commit()
        return self._view(actor, device.organization_id, device)

    # ------------------------------------------------------------- operators
    def list_actions(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> DeviceActionsResponse:
        # Any of the operate permissions is enough to open the panel; each action is filtered inside.
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        can_power = self.authz.check(membership, POWER_PERMISSION, device_group_id=device.device_group_id)
        can_users = self.authz.check(membership, USERS_PERMISSION, device_group_id=device.device_group_id)
        can_update = self.authz.check(membership, UPDATE_AGENT_PERMISSION, device_group_id=device.device_group_id)
        if not can_power and not can_users and not can_update:
            self.authz.require(membership, POWER_PERMISSION, device_group_id=device.device_group_id)
        return self._view(actor, organization_id, device)

    def reboot(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, POWER_PERMISSION)
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_REBOOT,
            audit_action="device.action.reboot",
            metadata={"name": device.name},
        )

    def shutdown(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, POWER_PERMISSION)
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_SHUTDOWN,
            audit_action="device.action.shutdown",
            metadata={"name": device.name},
        )

    def update_agent(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, UPDATE_AGENT_PERMISSION)
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_UPDATE_AGENT,
            from_version=device.agent_version,
            audit_action="device.action.update_agent",
            metadata={"name": device.name, "from_version": device.agent_version},
        )

    def set_password(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: SetPasswordRequest
    ) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, USERS_PERMISSION)
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_SET_PASSWORD,
            username=payload.username,
            password=payload.password,
            audit_action="device.action.set_password",
            metadata={"name": device.name, "username": payload.username},
        )

    def add_user(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: AddUserRequest
    ) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, USERS_PERMISSION)
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_ADD_USER,
            username=payload.username,
            password=payload.password,
            sudo=payload.sudo,
            authorized_key=payload.authorized_key,
            audit_action="device.action.add_user",
            metadata={
                "name": device.name,
                "username": payload.username,
                "sudo": payload.sudo,
                "has_authorized_key": payload.authorized_key is not None,
            },
        )

    # ----------------------------------------------------------------- agent
    def has_pending(self, device_id: uuid.UUID) -> bool:
        rows = self._pending(device_id)
        if self._expire(rows):
            self.session.commit()
            rows = [row for row in rows if row.state == STATE_PENDING]
        return bool(rows)

    def changes_for_agent(self, *, device: Device) -> AgentDeviceActionsResponse:
        rows = self._pending(device.id, for_update=True)
        dirty = self._expire(rows)
        now = datetime.now(UTC)
        actions: list[AgentDeviceAction] = []
        for row in rows:
            if row.state != STATE_PENDING:
                continue
            password: str | None = None
            if row.kind in USER_KINDS:
                try:
                    if row.password_sealed is None:
                        raise SecretBoxError("no password stored")
                    password = unseal(self.settings, row.password_sealed, context=_context(row))
                except SecretBoxError:
                    logger.warning("Device action password for %s cannot be read", row.id)
                    row.password_sealed = None
                    row.state = STATE_FAILED
                    row.last_error = _UNREADABLE
                    row.reported_at = now
                    dirty = True
                    continue
            # Handed out once: a restart must not run again after the device comes back.
            row.state = STATE_SENT
            row.sent_at = now
            row.password_sealed = None
            dirty = True
            actions.append(
                AgentDeviceAction(
                    id=row.id,
                    kind=row.kind,  # type: ignore[arg-type]
                    username=row.username,
                    password=password,
                    sudo=row.sudo,
                    authorized_key=row.authorized_key,
                    from_version=row.from_version,
                )
            )
        if dirty:
            self.session.commit()
        else:
            self.session.rollback()
        return AgentDeviceActionsResponse(actions=actions)

    def report_from_agent(self, *, device: Device, payload: AgentActionReportRequest) -> AgentActionReportResponse:
        now = datetime.now(UTC)
        accepted = 0
        if payload.results:
            rows = {
                row.id: row
                for row in self.session.scalars(
                    select(DeviceAction)
                    .where(DeviceAction.device_id == device.id, DeviceAction.state == STATE_SENT)
                    .with_for_update()
                )
            }
            for result in payload.results:
                row = rows.get(result.id)
                if row is None:
                    continue
                accepted += 1
                row.reported_at = now
                row.state = STATE_DONE if result.state == "done" else STATE_FAILED
                row.last_error = None if row.state == STATE_DONE else (result.error or "The device reported a failure.")
            self.session.commit()
        return AgentActionReportResponse(accepted=accepted)
