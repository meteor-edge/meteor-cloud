"""Desired vs installed software on devices."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.authorization.service import AuthzService
from app.core.exceptions import NotFoundError
from app.deployments.software_models import DeviceSoftware, SoftwareHealth, SoftwareSyncStatus
from app.deployments.software_repository import DeviceSoftwareRepository
from app.deployments.software_schemas import (
    DesiredSoftwareItem,
    DeviceSoftwareResponse,
    ReleaseSummary,
    SoftwareReportItem,
)
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.identity.models import User
from app.releases.kinds import workload_key_from_spec
from app.releases.models import Release
from app.releases.repository import ReleaseRepository


class DeviceSoftwareService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.software = DeviceSoftwareRepository(session)
        self.devices = DeviceRepository(session)
        self.releases = ReleaseRepository(session)
        self.authz = AuthzService(session)

    def _release_summary(self, release: Release | None) -> ReleaseSummary | None:
        if release is None:
            return None
        return ReleaseSummary(
            id=release.id,
            name=release.name,
            version=release.version,
            kind=release.kind,
        )

    def _to_response(self, row: DeviceSoftware) -> DeviceSoftwareResponse:
        desired = (
            self.releases.get(organization_id=row.organization_id, release_id=row.desired_release_id)
            if row.desired_release_id
            else None
        )
        installed = (
            self.releases.get(organization_id=row.organization_id, release_id=row.installed_release_id)
            if row.installed_release_id
            else None
        )
        return DeviceSoftwareResponse(
            id=row.id,
            organization_id=row.organization_id,
            device_id=row.device_id,
            workload_key=row.workload_key,
            kind=row.kind,
            desired_release_id=row.desired_release_id,
            desired_revision=row.desired_revision,
            desired_spec=row.desired_spec,
            desired_release=self._release_summary(desired),
            installed_release_id=row.installed_release_id,
            installed_version=row.installed_version,
            installed_release=self._release_summary(installed),
            health=row.health,
            sync_status=row.sync_status,
            progress_percent=row.progress_percent,
            attempt_count=row.attempt_count,
            last_error=row.last_error,
            reported_at=row.reported_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_for_device_operator(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> list[DeviceSoftwareResponse]:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        self.authz.require(membership, "device.read", device_group_id=device.device_group_id)
        return [self._to_response(row) for row in self.software.list_for_device(device_id=device_id)]

    def set_desired_software(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        release: Release,
        commit: bool = True,
    ) -> DeviceSoftware:
        """Point a device workload at a release. Used by tests and Phase 3 Deploy."""
        if release.organization_id != organization_id:
            raise NotFoundError("release_not_found", "Release was not found.")
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")

        workload_key = workload_key_from_spec(release.kind, release.spec)
        row = self.software.get_by_workload(device_id=device_id, workload_key=workload_key)
        if row is None:
            row = DeviceSoftware(
                organization_id=organization_id,
                device_id=device_id,
                workload_key=workload_key,
                kind=release.kind,
            )
            self.software.create(row)

        row.kind = release.kind
        row.desired_release_id = release.id
        row.desired_revision = int(row.desired_revision or 0) + 1
        row.desired_spec = dict(release.spec)
        row.sync_status = SoftwareSyncStatus.PENDING.value
        row.progress_percent = 0
        row.last_error = None
        if commit:
            self.session.commit()
            self.session.refresh(row)
        else:
            self.session.flush()
        return row

    def clear_desired_software(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        workload_key: str,
        commit: bool = True,
    ) -> DeviceSoftware | None:
        """Clear desired release for a workload (used when cancelling a Deploy)."""
        row = self.software.get_by_workload(device_id=device_id, workload_key=workload_key)
        if row is None or row.organization_id != organization_id:
            return None
        row.desired_release_id = None
        row.desired_revision = int(row.desired_revision or 0) + 1
        row.desired_spec = {}
        row.sync_status = SoftwareSyncStatus.CANCELLED.value
        row.progress_percent = 0
        row.last_error = None
        if commit:
            self.session.commit()
            self.session.refresh(row)
        else:
            self.session.flush()
        return row

    def artifact_is_desired_for_device(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> bool:
        """True when ``artifact_id`` belongs to an active desired release on the device."""
        for row in self.software.list_for_device(device_id=device_id):
            if row.organization_id != organization_id:
                continue
            if row.desired_release_id is None:
                continue
            if row.sync_status == SoftwareSyncStatus.CANCELLED.value:
                continue
            release = self.releases.get(
                organization_id=organization_id, release_id=row.desired_release_id
            )
            if release is not None and release.artifact_id == artifact_id:
                return True
        return False

    def require_desired_artifact(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> None:
        if not self.artifact_is_desired_for_device(
            organization_id=organization_id,
            device_id=device_id,
            artifact_id=artifact_id,
        ):
            raise NotFoundError("artifact_not_found", "Artifact was not found.")

    def list_desired_for_agent(self, *, device_id: uuid.UUID) -> list[DesiredSoftwareItem]:
        items: list[DesiredSoftwareItem] = []
        for row in self.software.list_for_device(device_id=device_id):
            if row.desired_release_id is None:
                continue
            if row.sync_status == SoftwareSyncStatus.CANCELLED.value:
                continue
            release = self.releases.get(
                organization_id=row.organization_id, release_id=row.desired_release_id
            )
            if release is None:
                continue
            items.append(
                DesiredSoftwareItem(
                    workload_key=row.workload_key,
                    kind=row.kind,
                    release_id=release.id,
                    release_name=release.name,
                    release_version=release.version,
                    artifact_id=release.artifact_id,
                    revision=row.desired_revision,
                    spec=row.desired_spec,
                )
            )
        return items

    def apply_software_reports(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        reports: list[SoftwareReportItem],
        commit: bool = True,
    ) -> None:
        """Apply agent reports. Unknown workloads are ignored."""
        now = datetime.now(UTC)
        for report in reports:
            row = self.software.get_by_workload(device_id=device_id, workload_key=report.workload_key)
            if row is None or row.organization_id != organization_id:
                continue

            if report.installed_release_id is not None:
                release = self.releases.get(
                    organization_id=organization_id, release_id=report.installed_release_id
                )
                if release is None:
                    continue
                row.installed_release_id = release.id
                row.installed_version = report.installed_version or release.version
            elif report.installed_version is not None:
                row.installed_version = report.installed_version

            row.health = report.health.value
            if report.progress_percent is not None:
                row.progress_percent = report.progress_percent
            if "last_error" in report.model_fields_set:
                row.last_error = report.last_error
            row.reported_at = now

            if report.sync_status is not None:
                row.sync_status = report.sync_status.value
            elif (
                row.desired_release_id is not None
                and row.installed_release_id == row.desired_release_id
                and row.health == SoftwareHealth.HEALTHY.value
            ):
                row.sync_status = SoftwareSyncStatus.IN_SYNC.value
                row.progress_percent = 100
                row.last_error = None
            elif row.health == SoftwareHealth.UNHEALTHY.value:
                row.sync_status = SoftwareSyncStatus.FAILED.value

            self._sync_deployment_progress(
                organization_id=organization_id,
                device_id=device_id,
                workload_key=row.workload_key,
            )
            self._sync_endpoint_availability(
                organization_id=organization_id,
                device_id=device_id,
                workload_key=row.workload_key,
            )

        if commit:
            self.session.commit()
        else:
            self.session.flush()

    def _sync_deployment_progress(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        workload_key: str,
    ) -> None:
        """Best-effort: advance open Deploy rows when agents report software status."""
        from app.deployments.service import DeploymentService

        DeploymentService(self.session).sync_progress_from_software(
            organization_id=organization_id,
            device_id=device_id,
            workload_key=workload_key,
        )

    def _sync_endpoint_availability(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        workload_key: str,
    ) -> None:
        from app.endpoints.service import EndpointService

        EndpointService(self.session).sync_from_software(
            organization_id=organization_id,
            device_id=device_id,
            workload_key=workload_key,
        )

    def apply_capabilities(self, *, device_id: uuid.UUID, capabilities: dict[str, Any]) -> None:
        """Store agent-reported capabilities on the device metadata (best-effort)."""
        device = self.session.get(Device, device_id)
        if device is None:
            return
        meta = dict(device.metadata_ or {})
        meta["capabilities"] = capabilities
        device.metadata_ = meta
