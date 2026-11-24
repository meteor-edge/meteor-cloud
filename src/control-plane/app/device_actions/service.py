"""One-off device actions from the console: restart, shut down, SSH user changes, agent repair.

An administrator orders an action; the device collects it once over its authenticated
API, applies it and reports back. A password is encrypted at rest and erased as soon
as the device collects it (or when the action expires). It never appears in a response,
a log line or the audit trail. Collecting marks the action sent so a restart is never
handed out twice after the device comes back.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, ValidationAppError
from app.core.secretbox import SecretBoxError, seal, unseal
from app.deployments.software_models import DeviceSoftware
from app.deployments.software_repository import DeviceSoftwareRepository
from app.device_actions.models import (
    KIND_ADD_USER,
    KIND_REBOOT,
    KIND_RESTART_MODULE,
    KIND_RUN_DIAGNOSTICS,
    KIND_SET_PASSWORD,
    KIND_SHUTDOWN,
    KIND_UPDATE_AGENT,
    KIND_WORKLOAD_SNAPSHOT,
    KIND_WORKLOAD_START,
    KIND_WORKLOAD_STOP,
    POWER_KINDS,
    REPAIR_KINDS,
    STATE_DONE,
    STATE_FAILED,
    STATE_PENDING,
    STATE_SENT,
    UPDATE_AGENT_KINDS,
    USER_KINDS,
    WORKLOAD_KINDS,
    WORKLOAD_MANAGE_KINDS,
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
    RestartModuleRequest,
    SetPasswordRequest,
    SoftwareActionRequest,
    SoftwareActionResponse,
)
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.identity.models import User
from app.mqtt.runtime import get_mqtt_publisher

logger = logging.getLogger(__name__)

POWER_PERMISSION = "device.reboot"
USERS_PERMISSION = "device.users"
UPDATE_AGENT_PERMISSION = "device.update_agent"
# Agent repair (restart a module, run diagnostics) is agent maintenance, like an update.
REPAIR_PERMISSION = UPDATE_AGENT_PERMISSION
_NOT_COLLECTED = "The device did not collect this action in time."
_UNREADABLE = "The stored password could not be read (the server's encryption key changed). Enter it again."
_MAX_PENDING = 5
SOFTWARE_PERMISSION = "software.manage"
# Workload actions per device and minute; looking again is counted on its own.
_WORKLOAD_PER_MINUTE = 10


def _context(row: DeviceAction) -> str:
    return f"device-action:{row.device_id}:{row.id}"


def advertised_actions(device: Device) -> list[str] | None:
    """The action kinds the device's agent says it can run, or None when it never said.

    Older agents report no ``capabilities.actions``; every kind stays allowed for them,
    and the agent reports a kind it does not know as ``failed: Unsupported``.
    """
    caps = (device.metadata_ or {}).get("capabilities")
    if not isinstance(caps, dict):
        return None
    kinds = caps.get("actions")
    if not isinstance(kinds, list):
        return None
    return [kind for kind in kinds if isinstance(kind, str)]


def reported_modules(device: Device) -> dict[str, dict] | None:
    """The agent's last module health map (``capabilities.modules``), or None."""
    caps = (device.metadata_ or {}).get("capabilities")
    if not isinstance(caps, dict):
        return None
    modules = caps.get("modules")
    return modules if isinstance(modules, dict) else None


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
            if row.expires_at > now:
                continue
            if row.state == STATE_PENDING:
                row.password_sealed = None
                row.state = STATE_FAILED
                row.last_error = _NOT_COLLECTED
                row.reported_at = now
                dirty = True
            elif row.state == STATE_SENT and row.kind == KIND_UPDATE_AGENT:
                # A lost collection response or unfinished updater must not block forever.
                row.state = STATE_FAILED
                row.last_error = "The device did not finish this agent update in time."
                row.reported_at = now
                dirty = True
            elif row.state == STATE_SENT and row.kind in WORKLOAD_KINDS:
                row.state = STATE_FAILED
                row.last_error = "The device did not report what happened."
                row.reported_at = now
                dirty = True
        return dirty

    def _expire_update_agent(self, device_id: uuid.UUID) -> bool:
        """Expire pending and sent update_agent rows past their TTL."""
        rows = list(
            self.session.scalars(
                select(DeviceAction).where(
                    DeviceAction.device_id == device_id,
                    DeviceAction.kind == KIND_UPDATE_AGENT,
                    DeviceAction.state.in_((STATE_PENDING, STATE_SENT)),
                )
            )
        )
        return self._expire(rows)

    def _to_response(self, row: DeviceAction) -> DeviceActionResponse:
        return DeviceActionResponse.model_validate(row)

    def _view(self, actor: User, organization_id: uuid.UUID, device: Device) -> DeviceActionsResponse:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        can_power = self.authz.check(membership, POWER_PERMISSION, device_group_id=device.device_group_id)
        can_users = self.authz.check(membership, USERS_PERMISSION, device_group_id=device.device_group_id)
        can_update = self.authz.check(membership, UPDATE_AGENT_PERMISSION, device_group_id=device.device_group_id)
        can_repair = self.authz.check(membership, REPAIR_PERMISSION, device_group_id=device.device_group_id)
        can_software = self.authz.check(membership, SOFTWARE_PERMISSION, device_group_id=device.device_group_id)
        statement = (
            select(DeviceAction)
            .where(DeviceAction.device_id == device.id, DeviceAction.kind != KIND_WORKLOAD_SNAPSHOT)
            .order_by(DeviceAction.created_at.desc())
            .limit(20)
        )
        rows = list(self.session.scalars(statement))
        if self._expire([row for row in rows if row.state in (STATE_PENDING, STATE_SENT)]):
            self.session.commit()
        actions = [
            self._to_response(row)
            for row in rows
            if (row.kind in POWER_KINDS and can_power)
            or (row.kind in USER_KINDS and can_users)
            or (row.kind in UPDATE_AGENT_KINDS and can_update)
            or (row.kind in REPAIR_KINDS and can_repair)
            or (row.kind in WORKLOAD_MANAGE_KINDS and can_software)
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
        module: str | None = None,
        audit_action: str,
        metadata: dict,
    ) -> DeviceActionsResponse:
        # Serialize enqueue per device so concurrent update orders see each other's rows.
        self.session.scalar(select(Device.id).where(Device.id == device.id).with_for_update())
        pending = self._pending(device.id, for_update=True)
        dirty = self._expire(pending)
        if kind == KIND_UPDATE_AGENT:
            dirty = self._expire_update_agent(device.id) or dirty
        if dirty:
            self.session.flush()
        pending = [row for row in pending if row.state == STATE_PENDING]
        # A user who may only read cannot fill the queue: snapshots do not count.
        pending = [row for row in pending if row.kind != KIND_WORKLOAD_SNAPSHOT]
        supported = advertised_actions(device)
        if supported is not None and kind not in supported:
            raise ConflictError(
                "action_unsupported",
                f"The agent on this device cannot run {kind!r}. Update meteorcli first.",
            )
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
        if kind in REPAIR_KINDS and any(row.kind == kind and row.module == module for row in pending):
            raise ConflictError(
                "repair_action_pending",
                "The same repair is already waiting for this device.",
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
            module=module,
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
        self._wake(device.id)
        return self._view(actor, device.organization_id, device)

    def _wake(self, device_id: uuid.UUID) -> None:
        """Tell a connected device that actions wait; otherwise its next heartbeat does. Best effort."""
        try:
            get_mqtt_publisher().publish(
                f"devices/{device_id}/commands", json.dumps({"type": "sync_actions"}), qos=1, retain=False
            )
        except Exception:
            logger.debug("MQTT sync_actions skipped for device %s", device_id, exc_info=True)

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
        can_software = self.authz.check(membership, SOFTWARE_PERMISSION, device_group_id=device.device_group_id)
        if not can_power and not can_users and not can_update and not can_software:
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
        if not device.agent_version:
            raise ValidationAppError(
                "agent_version_unknown",
                "This device has not reported a meteorcli version yet. Wait for a heartbeat, then try again.",
            )
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_UPDATE_AGENT,
            from_version=device.agent_version,
            audit_action="device.action.update_agent",
            metadata={"name": device.name, "from_version": device.agent_version},
        )

    def restart_module(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: RestartModuleRequest
    ) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, REPAIR_PERMISSION)
        modules = reported_modules(device)
        if modules is not None and payload.module not in modules:
            raise ValidationAppError(
                "module_unknown",
                f"The agent on this device has no module {payload.module!r}.",
            )
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_RESTART_MODULE,
            module=payload.module,
            audit_action="device.action.restart_module",
            metadata={"name": device.name, "module": payload.module},
        )

    def run_diagnostics(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceActionsResponse:
        device = self._require_device(actor, organization_id, device_id, REPAIR_PERMISSION)
        return self._enqueue(
            actor=actor,
            device=device,
            kind=KIND_RUN_DIAGNOSTICS,
            audit_action="device.action.run_diagnostics",
            metadata={"name": device.name},
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

    # -------------------------------------------------------------- software
    def _require_software(
        self, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, software_id: uuid.UUID, permission: str
    ) -> tuple[Device, DeviceSoftware]:
        device = self._require_device(actor, organization_id, device_id, permission)
        row = self.session.get(DeviceSoftware, software_id)
        if row is None or row.device_id != device.id or row.organization_id != organization_id:
            raise NotFoundError("software_not_found", "This device has no such software.")
        return device, row

    def order_software_action(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        software_id: uuid.UUID,
        payload: SoftwareActionRequest,
    ) -> SoftwareActionResponse:
        manage = payload.kind in WORKLOAD_MANAGE_KINDS
        device, software = self._require_software(
            actor, organization_id, device_id, software_id, SOFTWARE_PERMISSION if manage else "device.read"
        )
        # Unlike the older kinds, an agent that never said what it can do cannot do this.
        if payload.kind not in (advertised_actions(device) or []):
            raise ConflictError(
                "action_unsupported", "The agent on this device cannot manage software. Update meteorcli first."
            )
        if payload.service is not None and (software.kind != "docker_compose" or not manage):
            raise ValidationAppError("service_not_allowed", "Only a compose workload has services to choose from.")
        # One order at a time per device, so counting and coalescing see every row.
        self.session.scalar(select(Device.id).where(Device.id == device.id).with_for_update())
        pending = self._pending(device.id, for_update=True)
        if self._expire(pending):
            self.session.flush()
        for row in pending:
            if row.state == STATE_PENDING and (row.kind, row.workload_key, row.service) == (
                payload.kind,
                software.workload_key,
                payload.service,
            ):
                # The same order is already waiting: that one will do.
                self.session.commit()
                return self._software_response(row, actor.email)
        kinds = WORKLOAD_MANAGE_KINDS if manage else {KIND_WORKLOAD_SNAPSHOT}
        recent = self.session.scalar(
            select(func.count())
            .select_from(DeviceAction)
            .where(
                DeviceAction.device_id == device.id,
                DeviceAction.kind.in_(kinds),
                DeviceAction.created_at > datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        if (recent or 0) >= _WORKLOAD_PER_MINUTE:
            raise AppError(
                "too_many_software_actions",
                f"A device takes at most {_WORKLOAD_PER_MINUTE} software actions a minute. Try again shortly.",
                status_code=429,
                headers={"Retry-After": "60"},
            )
        if (
            manage
            and sum(r.state == STATE_PENDING and r.kind != KIND_WORKLOAD_SNAPSHOT for r in pending) >= _MAX_PENDING
        ):
            raise ConflictError(
                "too_many_pending_actions",
                f"A device can have at most {_MAX_PENDING} waiting actions. Wait for it to collect them.",
            )
        row = DeviceAction(
            organization_id=device.organization_id,
            device_id=device.id,
            kind=payload.kind,
            state=STATE_PENDING,
            workload_key=software.workload_key,
            service=payload.service,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.device_action_ttl_seconds),
            requested_by_user_id=actor.id,
        )
        self.session.add(row)
        self.session.flush()
        if manage:
            self.audit.record(
                actor=actor,
                organization_id=device.organization_id,
                action=f"software.{payload.kind.removeprefix('workload_')}",
                resource_type="device_software",
                resource_id=software.id,
                metadata={
                    "device_id": str(device.id),
                    "device": device.name,
                    "workload_key": software.workload_key,
                    "service": payload.service,
                },
            )
        self.session.commit()
        self._wake(device.id)
        return self._software_response(row, actor.email)

    def _software_response(self, row: DeviceAction, requested_by: str | None) -> SoftwareActionResponse:
        response = SoftwareActionResponse.model_validate(row)
        response.requested_by = requested_by
        return response

    def list_software_actions(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, software_id: uuid.UUID
    ) -> list[SoftwareActionResponse]:
        """Recent actions on one workload, newest first."""
        device, software = self._require_software(actor, organization_id, device_id, software_id, "device.read")
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        names = self.authz.check(membership, "member.read")
        rows = list(
            self.session.execute(
                select(DeviceAction, User.email)
                .outerjoin(User, User.id == DeviceAction.requested_by_user_id)
                .where(
                    DeviceAction.device_id == device.id,
                    DeviceAction.workload_key == software.workload_key,
                    DeviceAction.kind.in_(WORKLOAD_KINDS),
                )
                .order_by(DeviceAction.created_at.desc())
                .limit(50)
            )
        )
        if self._expire([row for row, _ in rows if row.state in (STATE_PENDING, STATE_SENT)]):
            self.session.commit()
        return [self._software_response(row, email if names else None) for row, email in rows]

    def _software_reported(self, device: Device, row: DeviceAction) -> None:
        """Show a finished stop or start at once; the next heartbeat confirms it."""
        if row.kind not in (KIND_WORKLOAD_STOP, KIND_WORKLOAD_START) or row.service or not row.workload_key:
            return
        software = DeviceSoftwareRepository(self.session).get_by_workload(
            device_id=device.id, workload_key=row.workload_key
        )
        if software is not None:
            software.stopped_by_user = row.kind == KIND_WORKLOAD_STOP
            software.health = "unknown"

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
                    module=row.module,
                    workload_key=row.workload_key,
                    service=row.service,
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
                if row.state == STATE_DONE:
                    self._software_reported(device, row)
                if row.kind == KIND_RUN_DIAGNOSTICS:
                    row.output = result.output
            self.session.commit()
        return AgentActionReportResponse(accepted=accepted)
