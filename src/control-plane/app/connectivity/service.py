"""WireGuard peer lifecycle: register, revoke, handshake, client config."""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.connectivity.ipam import allocate_address
from app.connectivity.models import ConnectivityStatus, DeviceConnectivity
from app.connectivity.repository import ConnectivityRepository
from app.connectivity.schemas import (
    AgentWireGuardRegisterRequest,
    AgentWireGuardRegisterResponse,
    DeviceConnectivityResponse,
    HandshakeReportItem,
    InternalPeer,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.identity.models import User
from app.ports.connectivity import DeviceConnectivityProvider
from app.tenancy.models import OrganizationMembership


class ConnectivityService:
    def __init__(
        self,
        session: Session,
        *,
        provider: DeviceConnectivityProvider | None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self.rows = ConnectivityRepository(session)
        self.devices = DeviceRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def _require_provider(self) -> DeviceConnectivityProvider:
        if self.provider is None:
            raise NotFoundError(
                "connectivity_disabled",
                "Device connectivity is disabled for this deployment.",
            )
        return self.provider

    def _require_membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        return self.authz.require_membership(user_id=user_id, organization_id=organization_id)

    def _to_response(self, row: DeviceConnectivity) -> DeviceConnectivityResponse:
        address = str(row.wg_address) if row.wg_address is not None else None
        return DeviceConnectivityResponse(
            device_id=row.device_id,
            organization_id=row.organization_id,
            provider=row.provider,
            wg_public_key=row.wg_public_key,
            wg_address=address,
            status=row.status,
            last_handshake_at=row.last_handshake_at,
            connected=self.handshake_is_fresh(row),
            server_address=self.settings.wireguard_server_address.split("/", 1)[0],
            ssh_user=self.settings.shell_ssh_user,
            ssh_port=self.settings.shell_ssh_port,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def handshake_is_fresh(self, row: DeviceConnectivity) -> bool:
        if row.status != ConnectivityStatus.ACTIVE.value or row.last_handshake_at is None:
            return False
        stale_after = timedelta(seconds=self.settings.connectivity_handshake_stale_seconds)
        seen = row.last_handshake_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        return datetime.now(UTC) - seen <= stale_after

    def register_agent(
        self,
        *,
        device: Device,
        payload: AgentWireGuardRegisterRequest,
    ) -> AgentWireGuardRegisterResponse:
        provider = self._require_provider()
        try:
            public_key = provider.validate_public_key(payload.public_key)
        except ValueError as exc:
            raise ValidationAppError("invalid_wireguard_key", str(exc)) from exc

        existing_key = self.rows.get_by_public_key(public_key)
        if existing_key is not None and existing_key.device_id != device.id:
            raise ConflictError(
                "wireguard_key_in_use",
                "This WireGuard public key is already registered to another device.",
            )

        row = self.rows.get(device.id)
        if row is None:
            address = allocate_address(
                cidr=self.settings.wireguard_client_cidr,
                server_address=self.settings.wireguard_server_address,
                allocated=self.rows.allocated_addresses(),
            )
            row = DeviceConnectivity(
                device_id=device.id,
                organization_id=device.organization_id,
                provider=provider.name,
                wg_public_key=public_key,
                wg_address=address,
                status=ConnectivityStatus.ACTIVE.value,
            )
            self.rows.add(row)
        else:
            if row.status == ConnectivityStatus.REVOKED.value:
                raise ForbiddenError(
                    "connectivity_revoked",
                    "Connectivity for this device was revoked. Re-enroll the device to reconnect.",
                )
            if row.wg_address is None:
                address = allocate_address(
                    cidr=self.settings.wireguard_client_cidr,
                    server_address=self.settings.wireguard_server_address,
                    allocated=self.rows.allocated_addresses(),
                )
                row.wg_address = address
            row.wg_public_key = public_key
            row.provider = provider.name
            row.status = ConnectivityStatus.ACTIVE.value
            row.revoked_at = None
        self.session.commit()
        self.session.refresh(row)
        address = str(row.wg_address)
        return AgentWireGuardRegisterResponse(
            status=row.status,
            provider=row.provider,
            wg_address=f"{address}/32",
            server_public_key=self.settings.wireguard_server_public_key,
            server_endpoint=self.settings.wireguard_server_endpoint,
            allowed_ips=provider.client_allowed_ips(self.settings.wireguard_server_address),
            persistent_keepalive=self.settings.wireguard_persistent_keepalive,
            ssh_user=self.settings.shell_ssh_user,
            ssh_gateway_public_key=self.settings.shell_gateway_public_key or None,
        )

    def get_for_operator(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceConnectivityResponse:
        membership = self._require_membership(organization_id, actor.id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, "device.read", device_group_id=device.device_group_id)
        self._require_provider()
        row = self.rows.get(device_id)
        if row is None:
            raise NotFoundError("connectivity_not_found", "This device has not registered connectivity yet.")
        return self._to_response(row)

    def list_for_operator(
        self, *, actor: User, organization_id: uuid.UUID
    ) -> list[DeviceConnectivityResponse]:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require_role_permission(membership, "device.read")
        self._require_provider()
        items: list[DeviceConnectivityResponse] = []
        for row in self.rows.list_for_organization(organization_id):
            device = self.devices.get(organization_id=organization_id, device_id=row.device_id)
            if device is None:
                continue
            if not self.authz.check(membership, "device.read", device_group_id=device.device_group_id):
                continue
            items.append(self._to_response(row))
        return items

    def revoke(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceConnectivityResponse:
        membership = self._require_membership(organization_id, actor.id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, "device.update", device_group_id=device.device_group_id)
        self._require_provider()
        row = self.rows.get(device_id)
        if row is None:
            raise NotFoundError("connectivity_not_found", "This device has not registered connectivity yet.")
        row.status = ConnectivityStatus.REVOKED.value
        row.revoked_at = datetime.now(UTC)
        row.wg_public_key = None
        row.wg_address = None
        from app.endpoints.service import EndpointService

        EndpointService(self.session, settings=self.settings).disable_for_device(
            organization_id=organization_id, device_id=device_id
        )
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="connectivity.revoke",
            resource_type="device",
            resource_id=device_id,
            metadata={"provider": row.provider},
        )
        self.session.commit()
        self.session.refresh(row)
        return self._to_response(row)

    def list_internal_peers(self) -> list[InternalPeer]:
        provider = self._require_provider()
        peers: list[InternalPeer] = []
        for row in self.rows.list_active_peers():
            if not row.wg_public_key or row.wg_address is None:
                continue
            peers.append(
                InternalPeer(
                    device_id=row.device_id,
                    organization_id=row.organization_id,
                    public_key=row.wg_public_key,
                    allowed_ips=provider.client_allowed_ips(str(row.wg_address)),
                    status=row.status,
                )
            )
        return peers

    def apply_handshakes(self, items: list[HandshakeReportItem]) -> int:
        self._require_provider()
        updated = 0
        for item in items:
            row = self.rows.get_by_public_key(item.public_key.strip())
            if row is None or row.status != ConnectivityStatus.ACTIVE.value:
                continue
            row.last_handshake_at = item.last_handshake_at or datetime.now(UTC)
            updated += 1
        self.session.commit()
        return updated

    def require_reachable(self, *, device: Device) -> DeviceConnectivity:
        row = self.rows.get(device.id)
        if row is None or row.status != ConnectivityStatus.ACTIVE.value or row.wg_address is None:
            raise ConflictError(
                "device_not_connected",
                "This device has no active WireGuard connectivity.",
            )
        return row

    def wg_host(self, row: DeviceConnectivity) -> str:
        return str(ipaddress.ip_address(str(row.wg_address).split("/", 1)[0]))
