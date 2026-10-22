"""WireGuard peer lifecycle: register, revoke, handshake, client config."""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
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

    def _server_allowed_ips(self) -> str:
        return f"{self.settings.wireguard_server_host}/32"

    def _client_config(self, *, address: str, public_key: str) -> str:
        keepalive = self.settings.wireguard_persistent_keepalive
        server_key = self.settings.wireguard_server_public_key
        endpoint = self.settings.wireguard_server_endpoint
        dns_line = ""
        if self.settings.wireguard_client_dns:
            dns_line = f"DNS = {self.settings.wireguard_client_dns}\n"
        return (
            "[Interface]\n"
            f"Address = {address}/32\n"
            f"PrivateKey = <device-private-key>\n"
            f"{dns_line}"
            "\n[Peer]\n"
            f"PublicKey = {server_key}\n"
            f"Endpoint = {endpoint}\n"
            f"AllowedIPs = {self._server_allowed_ips()}\n"
            f"PersistentKeepalive = {keepalive}\n"
            f"# Device public key (already registered): {public_key}\n"
        )

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
        if row is not None and row.status == ConnectivityStatus.REVOKED.value:
            # Revocation sticks until an operator restores connectivity.
            raise ForbiddenError(
                "connectivity_revoked",
                "WireGuard connectivity for this device was revoked by an operator.",
            )
        for attempt in range(3):
            try:
                row = self._activate(device=device, row=row, public_key=public_key, provider=provider)
                break
            except IntegrityError:
                # Two devices raced for the same free address; retry with a fresh view.
                self.session.rollback()
                if attempt == 2:
                    raise ConflictError(
                        "connectivity_allocation_conflict",
                        "Could not allocate a WireGuard address. Please retry.",
                    ) from None
                existing_key = self.rows.get_by_public_key(public_key)
                if existing_key is not None and existing_key.device_id != device.id:
                    raise ConflictError(
                        "wireguard_key_in_use",
                        "This WireGuard public key is already registered to another device.",
                    ) from None
                row = self.rows.get(device.id)
        address = str(row.wg_address)
        return AgentWireGuardRegisterResponse(
            status=row.status,
            provider=row.provider,
            wg_address=f"{address}/32",
            server_public_key=self.settings.wireguard_server_public_key,
            server_endpoint=self.settings.wireguard_server_endpoint,
            server_allowed_ips=self._server_allowed_ips(),
            allowed_ips=provider.client_allowed_ips(address),
            persistent_keepalive=self.settings.wireguard_persistent_keepalive,
            ssh_user=self.settings.shell_ssh_user,
            ssh_gateway_public_key=self.settings.shell_gateway_public_key or None,
            ssh_gateway_source=self._server_allowed_ips() if self.settings.shell_gateway_public_key else None,
            client_config=self._client_config(address=address, public_key=public_key),
        )

    def _activate(
        self,
        *,
        device: Device,
        row: DeviceConnectivity | None,
        public_key: str,
        provider: DeviceConnectivityProvider,
    ) -> DeviceConnectivity:
        if row is None:
            row = DeviceConnectivity(
                device_id=device.id,
                organization_id=device.organization_id,
                provider=provider.name,
                wg_public_key=public_key,
                wg_address=self._allocate(),
                status=ConnectivityStatus.ACTIVE.value,
            )
            self.rows.add(row)
        else:
            if row.wg_address is None:
                row.wg_address = self._allocate()
            row.wg_public_key = public_key
            row.provider = provider.name
            row.status = ConnectivityStatus.ACTIVE.value
            row.revoked_at = None
        self.session.commit()
        self.session.refresh(row)
        return row

    def _allocate(self) -> str:
        return allocate_address(
            cidr=self.settings.wireguard_client_cidr,
            server_address=self.settings.wireguard_server_address,
            allocated=self.rows.allocated_addresses(),
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
        # Keep wg_address: it stays reserved for this device so it is never handed to
        # another device while old host keys or routes may still point at it.
        from app.endpoints.service import EndpointService
        from app.shell.service import close_open_sessions_for_device

        EndpointService(self.session, settings=self.settings).disable_for_device(
            organization_id=organization_id, device_id=device_id
        )
        closed_shells = close_open_sessions_for_device(self.session, device_id=device_id)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="connectivity.revoke",
            resource_type="device",
            resource_id=device_id,
            metadata={"provider": row.provider, "closed_shell_sessions": closed_shells},
        )
        self.session.commit()
        self.session.refresh(row)
        return self._to_response(row)

    def restore(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceConnectivityResponse:
        """Allow a revoked device to register again. It becomes active on its next registration."""
        membership = self._require_membership(organization_id, actor.id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, "device.update", device_group_id=device.device_group_id)
        self._require_provider()
        row = self.rows.get(device_id)
        if row is None:
            raise NotFoundError("connectivity_not_found", "This device has not registered connectivity yet.")
        if row.status != ConnectivityStatus.REVOKED.value:
            raise ConflictError("connectivity_not_revoked", "Connectivity for this device is not revoked.")
        row.status = ConnectivityStatus.PENDING.value
        row.revoked_at = None
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="connectivity.restore",
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
