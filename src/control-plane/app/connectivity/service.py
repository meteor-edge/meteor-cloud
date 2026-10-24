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
    normalize_ssh_host_key,
    ssh_host_key_fingerprint,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.identity.models import User
from app.ports.connectivity import DeviceConnectivityProvider
from app.tenancy.models import OrganizationMembership

HOST_KEY_CHANGED = "Device SSH host key changed; re-enroll the device to trust the new key."
SSH_AUTH_CA = "ca"


def ssh_principal(device_id: uuid.UUID) -> str:
    """The only principal a device's cert-authority line accepts, so a certificate opens one device."""
    return f"meteorcloud-device-{device_id}"


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

    def _to_response(self, row: DeviceConnectivity, device: Device | None = None) -> DeviceConnectivityResponse:
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
            state=self.state_for(row, device),
            agent_last_seen_at=device.last_seen_at if device is not None else None,
            ssh_host_key_fingerprint=ssh_host_key_fingerprint(row.ssh_host_key),
            ssh_auth=row.ssh_auth or "key",
            last_ssh_ok_at=row.last_ssh_ok_at,
            last_ssh_error=row.last_ssh_error,
            last_ssh_error_at=row.last_ssh_error_at,
            server_address=self.settings.wireguard_server_address.split("/", 1)[0],
            ssh_user=self.ssh_user_for(row),
            ssh_port=self.settings.shell_ssh_port,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def ssh_user_for(self, row: DeviceConnectivity) -> str:
        return (row.ssh_user or "").strip() or self.settings.shell_ssh_user

    def state_for(self, row: DeviceConnectivity, device: Device | None) -> str:
        """Connectivity from recent evidence: the gateway's handshake, then the agent's check-in."""
        if row.status == ConnectivityStatus.REVOKED.value:
            return "revoked"
        if row.status != ConnectivityStatus.ACTIVE.value or not row.wg_public_key or row.wg_address is None:
            return "pending"
        if self.handshake_is_fresh(row):
            return "connected"
        seen = device.last_seen_at if device is not None else None
        if seen is not None:
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=UTC)
            if datetime.now(UTC) - seen <= timedelta(seconds=self.settings.device_offline_threshold_seconds):
                return "reconnecting"
        return "offline"

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
        try:
            row = self._register(device=device, payload=payload, provider=provider, public_key=public_key)
        except IntegrityError:
            # A concurrent registration took the row or the address first; retry once on fresh state.
            self.session.rollback()
            row = self._register(device=device, payload=payload, provider=provider, public_key=public_key)
        address = str(row.wg_address)
        return AgentWireGuardRegisterResponse(
            status=row.status,
            provider=row.provider,
            wg_address=f"{address}/32",
            server_public_key=self.settings.wireguard_server_public_key,
            server_endpoint=self.settings.wireguard_server_endpoint,
            allowed_ips=provider.client_allowed_ips(self.settings.wireguard_server_address),
            persistent_keepalive=self.settings.wireguard_persistent_keepalive,
            ssh_user=self.ssh_user_for(row),
            ssh_gateway_public_key=(
                None if self.settings.shell_gateway_key_retired else self.settings.shell_gateway_public_key or None
            ),
            ssh_gateway_key_retired=self.settings.shell_gateway_key_retired,
            ssh_ca_public_key=self.ssh_ca_public_key(),
            ssh_principal=ssh_principal(device.id) if self.ssh_ca_public_key() else None,
        )

    def ssh_ca_public_key(self) -> str | None:
        """The configured SSH user CA ("<type> <base64>"), when certificates can be signed."""
        if not (self.settings.shell_ssh_ca_file and self.settings.shell_ssh_ca_public_key.strip()):
            return None
        return normalize_ssh_host_key(self.settings.shell_ssh_ca_public_key)

    def _trusts_ca(self, reported: str | None) -> bool:
        ca = self.ssh_ca_public_key()
        return bool(ca and reported == ca)

    def uses_certificates(self, row: DeviceConnectivity) -> bool:
        """Sign a certificate for this device (it trusts the current CA)."""
        return row.ssh_auth == SSH_AUTH_CA and self.ssh_ca_public_key() is not None

    def _allocate(self) -> str:
        return allocate_address(
            cidr=self.settings.wireguard_client_cidr,
            server_address=self.settings.wireguard_server_address,
            allocated=self.rows.allocated_addresses(),
        )

    def _register(
        self,
        *,
        device: Device,
        payload: AgentWireGuardRegisterRequest,
        provider: DeviceConnectivityProvider,
        public_key: str,
    ) -> DeviceConnectivity:
        existing_key = self.rows.get_by_public_key(public_key)
        if existing_key is not None and existing_key.device_id != device.id:
            raise ConflictError(
                "wireguard_key_in_use",
                "This WireGuard public key is already registered to another device.",
            )

        row = self.rows.get(device.id)
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
            if row.status == ConnectivityStatus.REVOKED.value:
                raise ForbiddenError(
                    "connectivity_revoked",
                    "Connectivity for this device was revoked. An operator must re-enroll it to reconnect.",
                )
            if (
                row.status == ConnectivityStatus.ACTIVE.value
                and row.wg_public_key
                and row.wg_public_key != public_key
            ):
                # Keys never rotate on reconnect; a new key means a replaced or cloned device.
                self.audit.record(
                    actor=None,
                    organization_id=device.organization_id,
                    action="connectivity.key_change_rejected",
                    resource_type="device",
                    resource_id=device.id,
                    metadata={"provider": row.provider},
                )
                self.session.commit()
                raise ConflictError(
                    "wireguard_key_changed",
                    "This device registered a different WireGuard key. An operator must re-enroll it.",
                )
            if row.wg_address is None:
                row.wg_address = self._allocate()
            if row.wg_public_key != public_key:
                row.last_handshake_at = None
            row.wg_public_key = public_key
            row.provider = provider.name
            row.status = ConnectivityStatus.ACTIVE.value
            row.revoked_at = None
        if payload.ssh_user:
            row.ssh_user = payload.ssh_user
        if payload.ssh_host_key:
            self._pin_host_key(row, device, payload.ssh_host_key)
        row.ssh_auth = SSH_AUTH_CA if self._trusts_ca(payload.ssh_ca_public_key) else None
        self.session.commit()
        self.session.refresh(row)
        return row

    def _pin_host_key(self, row: DeviceConnectivity, device: Device, host_key: str) -> None:
        """Trust the first host key a device reports over its authenticated channel; never replace it."""
        if not row.ssh_host_key:
            row.ssh_host_key = host_key
            return
        if row.ssh_host_key == host_key or row.last_ssh_error == HOST_KEY_CHANGED:
            return
        row.last_ssh_error = HOST_KEY_CHANGED
        row.last_ssh_error_at = datetime.now(UTC)
        self.audit.record(
            actor=None,
            organization_id=device.organization_id,
            action="connectivity.host_key_mismatch",
            resource_type="device",
            resource_id=device.id,
            metadata={
                "pinned": ssh_host_key_fingerprint(row.ssh_host_key),
                "reported": ssh_host_key_fingerprint(host_key),
            },
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
        return self._to_response(row, device)

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
            items.append(self._to_response(row, device))
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
        return self._to_response(row, device)

    def reenroll(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceConnectivityResponse:
        """Explicit recovery (replaced device, reinstalled OS, revoked by mistake).

        Forgets the WireGuard key, address and pinned SSH host key. The next registration made
        with the device's own credential becomes active with a new key and address.
        """
        membership = self._require_membership(organization_id, actor.id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, "device.update", device_group_id=device.device_group_id)
        self._require_provider()
        row = self.rows.get(device_id)
        if row is None:
            raise NotFoundError("connectivity_not_found", "This device has not registered connectivity yet.")
        previous = row.status
        row.status = ConnectivityStatus.PENDING.value
        row.revoked_at = None
        row.wg_public_key = None
        row.wg_address = None
        row.ssh_host_key = None
        row.ssh_auth = None
        row.last_handshake_at = None
        row.last_ssh_error = None
        row.last_ssh_error_at = None
        from app.endpoints.service import EndpointService

        EndpointService(self.session, settings=self.settings).disable_for_device(
            organization_id=organization_id, device_id=device_id
        )
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="connectivity.reenroll",
            resource_type="device",
            resource_id=device_id,
            metadata={"provider": row.provider, "previous_status": previous},
        )
        self.session.commit()
        self.session.refresh(row)
        return self._to_response(row, device)

    def record_ssh_result(self, device_id: uuid.UUID, *, error: str | None) -> None:
        row = self.rows.get(device_id)
        if row is None:
            return
        now = datetime.now(UTC)
        if error:
            row.last_ssh_error = error[:255]
            row.last_ssh_error_at = now
        else:
            row.last_ssh_ok_at = now
            if row.last_ssh_error != HOST_KEY_CHANGED:
                row.last_ssh_error = None
        self.session.commit()

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
        if not device.is_enabled or device.credential_hash is None:
            raise ConflictError("device_disabled", "This device is disabled or its credential was revoked.")
        row = self.rows.get(device.id)
        if row is None or row.status != ConnectivityStatus.ACTIVE.value or row.wg_address is None:
            raise ConflictError(
                "device_not_connected",
                "This device has no active WireGuard connectivity.",
            )
        if not self.handshake_is_fresh(row):
            raise ConflictError(
                "device_offline",
                "The device's WireGuard tunnel is down (no recent handshake). Check that it is online.",
            )
        return row

    def wg_host(self, row: DeviceConnectivity) -> str:
        return str(ipaddress.ip_address(str(row.wg_address).split("/", 1)[0]))
