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
from app.deployments.repository import DeploymentRepository
from app.deployments.software_models import SoftwareHealth, SoftwareSyncStatus
from app.deployments.software_repository import DeviceSoftwareRepository
from app.devices.repository import DeviceRepository
from app.endpoints.models import ApplicationEndpoint, EndpointAvailability
from app.endpoints.repository import EndpointRepository
from app.endpoints.schemas import (
    EndpointCreate,
    EndpointResponse,
    EndpointUpdate,
    HostnameCheckResponse,
    HostnameSuggestionResponse,
)
from app.endpoints.validation import (
    HostnameRejected,
    assert_wireguard_upstream,
    normalize_domain,
    suggested_hostname,
    validate_hostname,
)
from app.identity.models import User
from app.tenancy.models import Organization, OrganizationMembership

MAX_SUGGESTION_ATTEMPTS = 50


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

    def _manual_availability(self, device_id: uuid.UUID) -> str:
        peer = self.connectivity.get(device_id)
        if peer is None or peer.status != ConnectivityStatus.ACTIVE.value:
            return EndpointAvailability.OFFLINE.value
        return EndpointAvailability.READY.value

    def _device(self, organization_id: uuid.UUID, device_id: uuid.UUID):
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        return device

    def _validate_hostname(self, hostname: str, *, organization_id: uuid.UUID, device) -> str:
        organization = self.session.get(Organization, organization_id)
        if organization is None:
            raise NotFoundError("organization_not_found", "Organization was not found.")
        return validate_hostname(
            hostname,
            apps_base_domain=self.settings.apps_base_domain,
            org_slug=organization.slug,
            device_name=device.name,
            reserved=self.settings.reserved_app_names,
        )

    def check_hostname(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        hostname: str,
        endpoint_id: uuid.UUID | None = None,
    ) -> HostnameCheckResponse:
        """Whether this organization can use ``hostname`` for an app on ``device_id`` (endpoint_id: renaming)."""
        membership = self._require_membership(organization_id, actor.id)
        device = self._device(organization_id, device_id)
        self.authz.require(membership, "endpoint.read", device_group_id=device.device_group_id)
        value = hostname.strip().lower().rstrip(".")
        try:
            value = self._validate_hostname(value, organization_id=organization_id, device=device)
        except HostnameRejected as exc:
            return HostnameCheckResponse(hostname=value, available=False, reason=exc.reason, message=exc.message)
        existing = self.endpoints.get_by_hostname(value)
        if existing is not None and existing.id != endpoint_id:
            return HostnameCheckResponse(
                hostname=value, available=False, reason="taken", message="This address is already in use."
            )
        return HostnameCheckResponse(hostname=value, available=True, reason="available", message="Available.")

    def suggest_hostname(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, app: str
    ) -> HostnameSuggestionResponse:
        """``<app>.<device>.<org>.<domain>``; a number is added to the app part while that is taken."""
        membership = self._require_membership(organization_id, actor.id)
        device = self._device(organization_id, device_id)
        self.authz.require(membership, "endpoint.read", device_group_id=device.device_group_id)
        organization = self.session.get(Organization, organization_id)
        if organization is None:
            raise NotFoundError("organization_not_found", "Organization was not found.")
        base = suggested_hostname(
            app=app,
            device_name=device.name,
            org_slug=organization.slug,
            apps_base_domain=self.settings.apps_base_domain,
        )
        label, rest = base.split(".", 1)
        for attempt in range(1, MAX_SUGGESTION_ATTEMPTS + 1):
            candidate = base if attempt == 1 else f"{label[: 63 - len(str(attempt)) - 1]}-{attempt}.{rest}"
            if self.endpoints.get_by_hostname(candidate) is None:
                return HostnameSuggestionResponse(
                    apps_domain=normalize_domain(self.settings.apps_base_domain), hostname=candidate, available=True
                )
        return HostnameSuggestionResponse(
            apps_domain=normalize_domain(self.settings.apps_base_domain), hostname=base, available=False
        )

    def _reject_reserved_port(self, port: int) -> None:
        # Public app routing must never front the device's SSH daemon.
        if port == self.settings.shell_ssh_port:
            raise ValidationAppError("reserved_port", f"Port {port} is the device SSH port and cannot be published.")

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
        self._reject_reserved_port(payload.port)
        hostname = self._validate_hostname(payload.hostname, organization_id=organization_id, device=device)
        if self.endpoints.get_by_hostname(hostname) is not None:
            raise ConflictError("hostname_taken", "This hostname is already registered.")
        if payload.enabled and self.endpoints.enabled_on_port(device_id=device.id, port=payload.port):
            raise ConflictError(
                "device_port_in_use",
                "An enabled endpoint already uses this port on the device.",
            )
        if payload.deployment_id is not None:
            deployment = DeploymentRepository(self.session).get(
                organization_id=organization_id,
                deployment_id=payload.deployment_id,
            )
            if deployment is None:
                raise NotFoundError("deployment_not_found", "Deployment was not found.")
        software_id = None
        if payload.workload_key:
            software = self.software.get_by_workload(device_id=device.id, workload_key=payload.workload_key)
            software_id = software.id if software is not None else None
        if payload.workload_key is None and payload.deployment_id is None:
            availability = self._manual_availability(device.id)
        else:
            availability = EndpointAvailability.UNKNOWN.value
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
            availability=availability,
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
            hostname = self._validate_hostname(payload.hostname, organization_id=organization_id, device=device)
            existing = self.endpoints.get_by_hostname(hostname)
            if existing is not None and existing.id != row.id:
                raise ConflictError("hostname_taken", "This hostname is already registered.")
            row.hostname = hostname
        if payload.port is not None:
            self._reject_reserved_port(payload.port)
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
                row.availability = self._manual_availability(device_id)
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
        """Enabled + ready endpoints whose upstream is the device WireGuard address.

        The address comes from the device's connectivity record, never from the endpoint itself.
        """
        result: list[tuple[ApplicationEndpoint, str]] = []
        cidr = self.settings.wireguard_client_cidr
        for row in self.endpoints.list_enabled_ready():
            if row.port == self.settings.shell_ssh_port:
                continue
            device = self.devices.get(organization_id=row.organization_id, device_id=row.device_id)
            if device is None or not device.is_enabled or device.credential_hash is None:
                continue
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
