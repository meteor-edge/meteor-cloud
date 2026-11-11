"""Change a device's Wi-Fi from the console, without holding its password longer than needed.

An administrator adds a network (or changes a password, or removes a network); the
device collects the change over its authenticated API, applies it and reports back.
The password is encrypted at rest and erased as soon as the device reports, or when
it was not collected in time. It never appears in a response, a log line or the audit
trail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.secretbox import SecretBoxError, seal, unseal
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.identity.models import User
from app.network.models import (
    ACTION_APPLY,
    ACTION_REMOVE,
    SECURITY_OPEN,
    STATE_APPLIED,
    STATE_FAILED,
    STATE_PENDING,
    STATE_ROLLED_BACK,
    DeviceWifiNetwork,
    DeviceWifiStatus,
)
from app.network.schemas import (
    AgentWifiChange,
    AgentWifiChangesResponse,
    AgentWifiReportRequest,
    AgentWifiReportResponse,
    DeviceWifiResponse,
    WifiNetworkRemoveRequest,
    WifiNetworkResponse,
    WifiNetworkSetRequest,
    WifiSavedNetwork,
    WifiStatusResponse,
)

logger = logging.getLogger(__name__)

PERMISSION = "device.network"
_MAX_NETWORKS_PER_DEVICE = 20
_NOT_COLLECTED = "The device did not collect this change in time, so the password was erased. Enter it again."
_UNREADABLE = "The stored password could not be read (the server's encryption key changed). Enter it again."


def _context(row: DeviceWifiNetwork) -> str:
    return f"wifi:{row.device_id}:{row.id}:{row.revision}"


class DeviceWifiService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.devices = DeviceRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    # ---------------------------------------------------------------- helpers
    def _require_device(self, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, permission: str) -> Device:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, permission, device_group_id=device.device_group_id)
        return device

    def _rows(self, device_id: uuid.UUID, *, for_update: bool = False) -> list[DeviceWifiNetwork]:
        statement = (
            select(DeviceWifiNetwork).where(DeviceWifiNetwork.device_id == device_id).order_by(DeviceWifiNetwork.ssid)
        )
        if for_update:
            statement = statement.with_for_update()
        return list(self.session.scalars(statement).all())

    def _erase_uncollected(self, rows: list[DeviceWifiNetwork]) -> bool:
        """Erase passwords the device never collected. Returns whether anything changed."""
        now = datetime.now(UTC)
        changed = False
        for row in rows:
            if row.state == STATE_PENDING and row.password_expires_at is not None and row.password_expires_at <= now:
                row.password_sealed = None
                row.password_expires_at = None
                row.state = STATE_FAILED
                row.last_error = _NOT_COLLECTED
                changed = True
        return changed

    @staticmethod
    def _to_response(row: DeviceWifiNetwork) -> WifiNetworkResponse:
        return WifiNetworkResponse.model_validate(row).model_copy(
            update={"password_pending": row.password_sealed is not None}
        )

    def _status(self, device_id: uuid.UUID) -> DeviceWifiStatus | None:
        return self.session.get(DeviceWifiStatus, device_id)

    def _view(self, device_id: uuid.UUID, rows: list[DeviceWifiNetwork]) -> DeviceWifiResponse:
        status = self._status(device_id)
        return DeviceWifiResponse(
            status=None
            if status is None
            else WifiStatusResponse(
                supported=status.supported,
                unsupported_reason=status.unsupported_reason,
                connected_ssid=status.connected_ssid,
                signal_percent=status.signal_percent,
                interface=status.interface,
                wired_connected=status.wired_connected,
                saved_networks=[WifiSavedNetwork.model_validate(item) for item in status.saved_networks or []],
                reported_at=status.reported_at,
            ),
            networks=[self._to_response(row) for row in rows],
        )

    # ------------------------------------------------------------- operators
    def get(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> DeviceWifiResponse:
        device = self._require_device(actor, organization_id, device_id, "device.read")
        rows = self._rows(device.id)
        if self._erase_uncollected(rows):
            self.session.commit()
        return self._view(device.id, rows)

    def set_network(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: WifiNetworkSetRequest
    ) -> DeviceWifiResponse:
        device = self._require_device(actor, organization_id, device_id, PERMISSION)
        rows = self._rows(device.id, for_update=True)
        row = next((item for item in rows if item.ssid == payload.ssid), None)
        if row is None:
            if len(rows) >= _MAX_NETWORKS_PER_DEVICE:
                raise ConflictError(
                    "too_many_wifi_networks",
                    f"A device can have at most {_MAX_NETWORKS_PER_DEVICE} managed Wi-Fi networks. Remove one first.",
                )
            # The id is needed before the password can be sealed to this row.
            row = DeviceWifiNetwork(
                id=uuid.uuid4(), organization_id=organization_id, device_id=device.id, ssid=payload.ssid, revision=0
            )
            self.session.add(row)
            rows.append(row)
        row.security = payload.security
        row.hidden = payload.hidden
        row.connect_now = payload.connect_now
        row.action = ACTION_APPLY
        row.state = STATE_PENDING
        row.revision += 1
        row.last_error = None
        row.reported_at = None
        row.updated_by_user_id = actor.id
        if payload.security == SECURITY_OPEN:
            row.password_sealed = None
            row.password_expires_at = None
        else:
            row.password_sealed = seal(self.settings, payload.password or "", context=_context(row))
            row.password_expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.wifi_password_ttl_seconds)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="device.wifi.set",
            resource_type="device",
            resource_id=device.id,
            metadata={"ssid": row.ssid, "security": row.security, "connect_now": row.connect_now},
        )
        self.session.commit()
        rows.sort(key=lambda item: item.ssid)
        return self._view(device.id, rows)

    def remove_network(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: WifiNetworkRemoveRequest
    ) -> DeviceWifiResponse:
        """Ask the device to forget a network, managed here or not."""
        device = self._require_device(actor, organization_id, device_id, PERMISSION)
        status = self._status(device.id)
        if status is not None and status.connected_ssid == payload.ssid and not status.wired_connected:
            raise ConflictError(
                "wifi_network_in_use",
                "The device is connected through this network and has no other connection, so removing it "
                "would cut it off. Add the new network first, or connect a cable.",
            )
        rows = self._rows(device.id, for_update=True)
        row = next((item for item in rows if item.ssid == payload.ssid), None)
        on_device = status is not None and any(item.get("ssid") == payload.ssid for item in status.saved_networks or [])
        never_reached_device = row is not None and row.action == ACTION_APPLY and row.state in {STATE_PENDING, STATE_FAILED}
        if row is None and not on_device:
            raise NotFoundError("wifi_network_not_found", "The device has no Wi-Fi network with this name.")
        if row is not None and never_reached_device and not on_device:
            # Nothing on the device to forget: withdraw the change.
            self.session.delete(row)
            rows.remove(row)
        else:
            if row is None:
                row = DeviceWifiNetwork(
                    id=uuid.uuid4(), organization_id=organization_id, device_id=device.id, ssid=payload.ssid, revision=0
                )
                self.session.add(row)
                rows.append(row)
            row.action = ACTION_REMOVE
            row.state = STATE_PENDING
            row.revision += 1
            row.password_sealed = None
            row.password_expires_at = None
            row.last_error = None
            row.reported_at = None
            row.updated_by_user_id = actor.id
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="device.wifi.remove",
            resource_type="device",
            resource_id=device.id,
            metadata={"ssid": payload.ssid},
        )
        self.session.commit()
        rows.sort(key=lambda item: item.ssid)
        return self._view(device.id, rows)

    # ----------------------------------------------------------------- agent
    def has_pending(self, device_id: uuid.UUID) -> bool:
        statement = select(func.count()).where(
            DeviceWifiNetwork.device_id == device_id, DeviceWifiNetwork.state == STATE_PENDING
        )
        return bool(self.session.scalar(statement))

    def changes_for_agent(self, *, device: Device) -> AgentWifiChangesResponse:
        rows = self._rows(device.id, for_update=True)
        dirty = self._erase_uncollected(rows)
        changes: list[AgentWifiChange] = []
        for row in rows:
            if row.state != STATE_PENDING:
                continue
            password: str | None = None
            if row.action == ACTION_APPLY and row.security != SECURITY_OPEN:
                try:
                    if row.password_sealed is None:
                        raise SecretBoxError("no password stored")
                    password = unseal(self.settings, row.password_sealed, context=_context(row))
                except SecretBoxError:
                    logger.warning("Wi-Fi password for device %s network %s cannot be read", device.id, row.id)
                    row.password_sealed = None
                    row.password_expires_at = None
                    row.state = STATE_FAILED
                    row.last_error = _UNREADABLE
                    dirty = True
                    continue
            changes.append(
                AgentWifiChange(
                    id=row.id,
                    revision=row.revision,
                    action=row.action,  # type: ignore[arg-type]
                    ssid=row.ssid,
                    security=row.security,
                    hidden=row.hidden,
                    connect_now=row.connect_now,
                    password=password,
                )
            )
        if dirty:
            self.session.commit()
        else:
            self.session.rollback()  # release the row locks
        return AgentWifiChangesResponse(changes=changes)

    def report_from_agent(self, *, device: Device, payload: AgentWifiReportRequest) -> AgentWifiReportResponse:
        now = datetime.now(UTC)
        accepted = 0
        if payload.results:
            rows = {row.id: row for row in self._rows(device.id, for_update=True)}
            for result in payload.results:
                row = rows.get(result.id)
                # A report about an older change must not settle a newer one.
                if row is None or row.revision != result.revision or row.state != STATE_PENDING:
                    continue
                accepted += 1
                if result.state == "removed" or (result.state == "applied" and row.action == ACTION_REMOVE):
                    self.session.delete(row)
                    continue
                # Whatever happened, the device has had the password: erase the server's copy.
                row.password_sealed = None
                row.password_expires_at = None
                row.reported_at = now
                row.state = {"applied": STATE_APPLIED, "rolled_back": STATE_ROLLED_BACK}.get(result.state, STATE_FAILED)
                row.last_error = None if row.state == STATE_APPLIED else (result.error or "The device reported a failure.")
        if payload.status is not None:
            status = self._status(device.id)
            if status is None:
                status = DeviceWifiStatus(device_id=device.id, reported_at=now)
                self.session.add(status)
            reported = payload.status
            status.supported = reported.supported
            status.unsupported_reason = reported.unsupported_reason
            status.connected_ssid = reported.connected_ssid
            status.signal_percent = reported.signal_percent
            status.interface = reported.interface
            status.wired_connected = reported.wired_connected
            status.saved_networks = [item.model_dump() for item in reported.saved_networks]
            status.reported_at = now
        self.session.commit()
        return AgentWifiReportResponse(accepted=accepted)

