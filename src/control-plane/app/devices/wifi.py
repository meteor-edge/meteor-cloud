"""Per-device WiFi desired state (post-enroll)."""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationAppError
from app.devices.models import Device, DeviceWifiSettings
from app.devices.repository import DeviceRepository, DeviceWifiSettingsRepository
from app.devices.schemas import (
    AgentWifiDesiredResponse,
    AgentWifiReport,
    DeviceWifiResponse,
    DeviceWifiUpdateRequest,
)
from app.devices.secrets import decrypt_secret, encrypt_secret
from app.identity.models import User
from app.mqtt.runtime import get_mqtt_publisher
from app.tenancy.models import OrganizationMembership

logger = logging.getLogger(__name__)


def _org_response(row: DeviceWifiSettings) -> DeviceWifiResponse:
    return DeviceWifiResponse(
        device_id=row.device_id,
        ssid=row.ssid,
        interface=row.interface,
        hidden=row.hidden,
        psk_set=bool(row.psk_ciphertext),
        revision=row.revision,
        applied_ssid=row.applied_ssid,
        applied_revision=row.applied_revision,
        sync_status=row.sync_status,
        sync_error=row.sync_error,
    )


class DeviceWifiService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.authz = AuthzService(session)
        self.devices = DeviceRepository(session)
        self.wifi = DeviceWifiSettingsRepository(session)
        self.audit = AuditRecorder(session)

    def _membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        return self.authz.require_membership(user_id=user_id, organization_id=organization_id)

    def _device(self, organization_id: uuid.UUID, device_id: uuid.UUID) -> Device:
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        return device

    def get(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> DeviceWifiResponse | None:
        membership = self._membership(organization_id, actor.id)
        device = self._device(organization_id, device_id)
        self.authz.require(membership, "device.read", device_group_id=device.device_group_id)
        row = self.wifi.get_for_device(device_id=device_id)
        return _org_response(row) if row is not None else None

    def upsert(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        payload: DeviceWifiUpdateRequest,
    ) -> DeviceWifiResponse:
        membership = self._membership(organization_id, actor.id)
        device = self._device(organization_id, device_id)
        self.authz.require(membership, "device.update", device_group_id=device.device_group_id)

        row = self.wifi.get_for_device(device_id=device_id)
        ciphertext = encrypt_secret(self.settings, payload.psk)
        if row is None:
            row = DeviceWifiSettings(
                organization_id=organization_id,
                device_id=device_id,
                ssid=payload.ssid,
                interface=payload.interface,
                hidden=payload.hidden,
                psk_ciphertext=ciphertext,
                revision=1,
                sync_status="pending",
                sync_error=None,
                applied_ssid=None,
                applied_revision=None,
            )
        else:
            row.ssid = payload.ssid
            row.interface = payload.interface
            row.hidden = payload.hidden
            row.psk_ciphertext = ciphertext
            row.revision = int(row.revision) + 1
            row.sync_status = "pending"
            row.sync_error = None
        self.wifi.upsert(row)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="device.wifi.update",
            resource_type="device",
            resource_id=device_id,
            metadata={
                "ssid": payload.ssid,
                "interface": payload.interface,
                "hidden": payload.hidden,
                "revision": row.revision,
            },
        )
        self.session.commit()
        self.session.refresh(row)
        self._notify_sync_network(device_id=device_id, revision=row.revision)
        return _org_response(row)

    def clear(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> None:
        membership = self._membership(organization_id, actor.id)
        device = self._device(organization_id, device_id)
        self.authz.require(membership, "device.update", device_group_id=device.device_group_id)
        row = self.wifi.get_for_device(device_id=device_id)
        if row is None:
            return
        revision = int(row.revision) + 1
        self.wifi.delete(row)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="device.wifi.clear",
            resource_type="device",
            resource_id=device_id,
            metadata={"revision": revision},
        )
        self.session.commit()
        self._notify_sync_network(device_id=device_id, revision=revision)

    def desired_for_agent(self, *, device: Device) -> AgentWifiDesiredResponse:
        if not device.is_enabled:
            raise NotFoundError("device_disabled", "Device is disabled.")
        row = self.wifi.get_for_device(device_id=device.id)
        if row is None or not row.psk_ciphertext:
            return AgentWifiDesiredResponse(configured=False)
        try:
            psk = decrypt_secret(self.settings, row.psk_ciphertext)
        except ValueError as exc:
            raise ValidationAppError(
                "wifi_secret_unavailable",
                "WiFi credentials could not be decrypted on the server.",
            ) from exc
        return AgentWifiDesiredResponse(
            configured=True,
            ssid=row.ssid,
            psk=psk,
            interface=row.interface,
            hidden=row.hidden,
            revision=row.revision,
        )

    def apply_report(self, *, device: Device, report: AgentWifiReport) -> None:
        row = self.wifi.get_for_device(device_id=device.id)
        if row is None:
            return
        if report.revision < row.revision:
            # Stale report for an older desired revision.
            return
        if report.revision > row.revision:
            return
        row.applied_revision = report.revision
        row.applied_ssid = report.applied_ssid
        row.sync_status = report.sync_status
        row.sync_error = report.sync_error
        self.wifi.upsert(row)

    def _notify_sync_network(self, *, device_id: uuid.UUID, revision: int) -> None:
        payload = json.dumps({"type": "sync_network", "revision": revision})
        try:
            publisher = get_mqtt_publisher()
            publisher.publish(
                f"devices/{device_id}/commands",
                payload,
                qos=1,
                retain=False,
            )
        except Exception:
            logger.debug("MQTT sync_network notify skipped for device %s", device_id, exc_info=True)
