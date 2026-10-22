"""Organization-scoped public application endpoints."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.connectivity.models import ConnectivityStatus
from app.connectivity.repository import ConnectivityRepository
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.deployments.software_models import SoftwareHealth, SoftwareSyncStatus
from app.deployments.software_repository import DeviceSoftwareRepository
from app.devices.repository import DeviceRepository
from app.endpoints.models import ApplicationEndpoint, EndpointAvailability
from app.endpoints.repository import EndpointRepository
from app.endpoints.schemas import EndpointCreate, EndpointResponse, EndpointUpdate
from app.endpoints.validation import assert_wireguard_upstream, validate_hostname
from app.identity.models import User
from app.tenancy.models import OrganizationMembership


class EndpointService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.endpoints = EndpointRepository(session)
        self.devices = DeviceRepository(session)
        self.connectivity = ConnectivityRepository(session)
        self.software = DeviceSoftwareRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def _require_membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        return self.authz.require_membership(user_id=user_id, organization_id=organization_id)

    def _device(self, organization_id: uuid.UUID, device_id: uuid.UUID):
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        return device

    def _public_url(self, hostname: str) -> str:
        return f"https://{hostname}"

    def _upstream_url(self, row: ApplicationEndpoint) -> str | None:
        peer = self.connectivity.get(row.device_id)
        if peer is None or peer.wg_address is None or peer.status != ConnectivityStatus.ACTIVE.value:
            return None
        host = str(peer.wg_address).split("/", 1)[0]
        return f"{row.protocol}://{host}:{row.port}"

    def _to_response(self, row: ApplicationEndpoint) -> EndpointResponse:
        return EndpointResponse(
            id=row.id,
            organization_id=row.organization_id,
            device_id=row.device_id,
            deployment_id=row.deployment_id,
            device_software_id=row.device_software_id,
            workload_key=row.workload_key,
            hostname=row.hostname,
            protocol=row.protocol,
            port=row.port,
            enabled=row.enabled,
            availability=row.availability,
            public_url=self._public_url(row.hostname),
            upstream_url=self._upstream_url(row),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_endpoints(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID | None = None,
    ) -> list[EndpointResponse]:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require_role_permission(membership, "endpoint.read")
        items: list[EndpointResponse] = []
        for row in self.endpoints.list_for_organization(
            organization_id=organization_id, device_id=device_id
        ):
            device = self.devices.get(organization_id=organization_id, device_id=row.device_id)
            if device is None:
                continue
            if not self.authz.check(membership, "endpoint.read", device_group_id=device.device_group_id):
                continue
            items.append(self._to_response(row))
        return items

    def get_endpoint(
        self, *, actor: User, organization_id: uuid.UUID, endpoint_id: uuid.UUID
    ) -> EndpointResponse:
        membership = self._require_membership(organization_id, actor.id)
        row = self.endpoints.get(organization_id=organization_id, endpoint_id=endpoint_id)
        if row is None:
            raise NotFoundError("endpoint_not_found", "Endpoint was not found.")
        device = self._device(organization_id, row.device_id)
        self.authz.require(membership, "endpoint.read", device_group_id=device.device_group_id)
        return self._to_response(row)

    def create_endpoint(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: EndpointCreate,
        commit: bool = True,
    ) -> EndpointResponse:
        membership = self._require_membership(organization_id, actor.id)
        device = self._device(organization_id, payload.device_id)
        self.authz.require(membership, "endpoint.create", device_group_id=device.device_group_id)
        hostname = validate_hostname(payload.hostname, apps_base_domain=self.settings.apps_base_domain)
        if self.endpoints.get_by_hostname(hostname) is not None:
            raise ConflictError("hostname_taken", "This hostname is already registered.")
        if payload.enabled and self.endpoints.enabled_on_port(device_id=device.id, port=payload.port):
            raise ConflictError(
                "device_port_in_use",
                "An enabled endpoint already uses this port on the device.",
            )
        software_id = None
        if payload.workload_key:
            software = self.software.get_by_workload(device_id=device.id, workload_key=payload.workload_key)
            software_id = software.id if software is not None else None
        row = ApplicationEndpoint(
            organization_id=organization_id,
            device_id=device.id,
            deployment_id=payload.deployment_id,
            device_software_id=software_id,
            workload_key=payload.workload_key,
            hostname=hostname,
            protocol=payload.protocol,
            port=payload.port,
            enabled=payload.enabled,
            availability=EndpointAvailability.UNKNOWN.value,
        )
        self.endpoints.add(row)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="endpoint.create",
            resource_type="endpoint",
            resource_id=row.id,
            metadata={"hostname": hostname, "device_id": str(device.id), "port": payload.port},
        )
        try:
            if commit:
                self.session.commit()
            else:
                self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("hostname_taken", "This hostname or device port is already registered.") from exc
        self.session.refresh(row)
        return self._to_response(row)

    def update_endpoint(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        endpoint_id: uuid.UUID,
        payload: EndpointUpdate,
    ) -> EndpointResponse:
        membership = self._require_membership(organization_id, actor.id)
        row = self.endpoints.get(organization_id=organization_id, endpoint_id=endpoint_id)
        if row is None:
            raise NotFoundError("endpoint_not_found", "Endpoint was not found.")
        device = self._device(organization_id, row.device_id)
        self.authz.require(membership, "endpoint.update", device_group_id=device.device_group_id)
        if payload.hostname is not None:
            hostname = validate_hostname(payload.hostname, apps_base_domain=self.settings.apps_base_domain)
            existing = self.endpoints.get_by_hostname(hostname)
            if existing is not None and existing.id != row.id:
                raise ConflictError("hostname_taken", "This hostname is already registered.")
            row.hostname = hostname
        if payload.port is not None:
            clash = self.endpoints.enabled_on_port(device_id=row.device_id, port=payload.port)
            if clash is not None and clash.id != row.id and (payload.enabled is not False) and row.enabled:
                raise ConflictError(
                    "device_port_in_use",
                    "An enabled endpoint already uses this port on the device.",
                )
            row.port = payload.port
        if payload.protocol is not None:
            row.protocol = payload.protocol
        if payload.enabled is not None:
            if payload.enabled:
                clash = self.endpoints.enabled_on_port(device_id=row.device_id, port=row.port)
                if clash is not None and clash.id != row.id:
                    raise ConflictError(
                        "device_port_in_use",
                        "An enabled endpoint already uses this port on the device.",
                    )
            row.enabled = payload.enabled
            action = "endpoint.disable" if not payload.enabled else "endpoint.update"
        else:
            action = "endpoint.update"
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action=action,
            resource_type="endpoint",
            resource_id=row.id,
            metadata={"hostname": row.hostname, "enabled": row.enabled},
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("hostname_taken", "This hostname or device port is already registered.") from exc
        self.session.refresh(row)
        return self._to_response(row)

    def delete_endpoint(
        self, *, actor: User, organization_id: uuid.UUID, endpoint_id: uuid.UUID
    ) -> None:
        membership = self._require_membership(organization_id, actor.id)
        row = self.endpoints.get(organization_id=organization_id, endpoint_id=endpoint_id)
        if row is None:
            raise NotFoundError("endpoint_not_found", "Endpoint was not found.")
        device = self._device(organization_id, row.device_id)
        self.authz.require(membership, "endpoint.delete", device_group_id=device.device_group_id)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="endpoint.delete",
            resource_type="endpoint",
            resource_id=row.id,
            metadata={"hostname": row.hostname},
        )
        self.endpoints.delete(row)
        self.session.commit()

    def disable_for_device(self, *, organization_id: uuid.UUID, device_id: uuid.UUID) -> None:
        for row in self.endpoints.list_for_device(device_id=device_id):
            if row.organization_id != organization_id:
                continue
            row.enabled = False
            row.availability = EndpointAvailability.OFFLINE.value

    def sync_from_software(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        workload_key: str,
    ) -> None:
        software = self.software.get_by_workload(device_id=device_id, workload_key=workload_key)
        for row in self.endpoints.list_for_device(device_id=device_id):
            if row.organization_id != organization_id:
                continue
            if row.workload_key and row.workload_key != workload_key:
                continue
            if row.workload_key is None and row.deployment_id is None:
                continue
            peer = self.connectivity.get(device_id)
            if peer is None or peer.status != ConnectivityStatus.ACTIVE.value:
                row.availability = EndpointAvailability.OFFLINE.value
                continue
            if software is None:
                continue
            if (
                software.sync_status == SoftwareSyncStatus.IN_SYNC.value
                and software.health == SoftwareHealth.HEALTHY.value
            ):
                row.availability = EndpointAvailability.READY.value
            elif software.sync_status == SoftwareSyncStatus.FAILED.value:
                row.availability = EndpointAvailability.UNHEALTHY.value
            else:
                row.availability = EndpointAvailability.UNKNOWN.value

    def routable_upstreams(self) -> list[tuple[ApplicationEndpoint, str]]:
        """Enabled + ready endpoints whose upstream is the device WireGuard address."""
        result: list[tuple[ApplicationEndpoint, str]] = []
        cidr = self.settings.wireguard_client_cidr
        for row in self.endpoints.list_enabled_ready():
            peer = self.connectivity.get(row.device_id)
            if peer is None or peer.wg_address is None or peer.status != ConnectivityStatus.ACTIVE.value:
                continue
            host = str(peer.wg_address).split("/", 1)[0]
            try:
                assert_wireguard_upstream(host, client_cidr=cidr)
            except ValidationAppError:
                continue
            result.append((row, f"{row.protocol}://{host}:{row.port}"))
        return result
