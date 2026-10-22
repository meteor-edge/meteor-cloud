"""Native MeteorCloud deployment provider.

Executes Deploys that MeteorCloud has already recorded: writes desired software
and optionally wakes agents via MQTT ``sync_desired``. Does not own the release
catalog or Deployments list/detail history.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.deployments.models import DeploymentDeviceStatus, DeploymentStatus
from app.deployments.repository import DeploymentRepository
from app.deployments.software_schemas import SoftwareReportItem
from app.deployments.software_service import DeviceSoftwareService
from app.mqtt.runtime import get_mqtt_publisher
from app.ports.deployment import DeploymentSnapshot, DeviceSoftwareView
from app.ports.mqtt import MQTTGateway
from app.releases.kinds import workload_key_from_spec
from app.releases.repository import ReleaseRepository

logger = logging.getLogger(__name__)


class NativeDeploymentProvider:
    """DeploymentProvider backed by MeteorCloud desired-software + MQTT."""

    def __init__(
        self,
        session: Session,
        *,
        publisher: MQTTGateway | None = None,
    ) -> None:
        self.session = session
        self.software = DeviceSoftwareService(session)
        self.releases = ReleaseRepository(session)
        self.deployments = DeploymentRepository(session)
        self._publisher = publisher

    def _publisher_or_default(self) -> MQTTGateway:
        if self._publisher is not None:
            return self._publisher
        return get_mqtt_publisher()

    def deploy(
        self,
        *,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
        release_id: uuid.UUID,
        device_ids: list[uuid.UUID],
        rollout: dict[str, Any],
        created_by_user_id: uuid.UUID,
    ) -> str:
        del rollout, created_by_user_id  # recorded on the MeteorCloud deployment row
        release = self.releases.get(organization_id=organization_id, release_id=release_id)
        if release is None:
            raise NotFoundError("release_not_found", "Release was not found.")
        deployment = self.deployments.get(organization_id=organization_id, deployment_id=deployment_id)
        if deployment is None:
            raise NotFoundError("deployment_not_found", "Deployment was not found.")
        if not device_ids:
            raise AppError("no_deploy_targets", "No devices to deploy to.", status_code=422)

        for device_id in device_ids:
            row = self.software.set_desired_software(
                organization_id=organization_id,
                device_id=device_id,
                release=release,
                commit=False,
            )
            self._notify_sync_desired(device_id=device_id, revision=row.desired_revision)

        self.session.flush()
        # Native has no external id; MeteorCloud deployment id is the source of truth.
        return str(deployment_id)

    def cancel(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
        deployment = self.deployments.get(organization_id=organization_id, deployment_id=deployment_id)
        if deployment is None:
            raise NotFoundError("deployment_not_found", "Deployment was not found.")
        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            raise ConflictError(
                "deployment_not_cancellable",
                "Only in-progress deployments can be cancelled.",
            )

        release = self.releases.get(organization_id=organization_id, release_id=deployment.release_id)
        workload_key = (
            workload_key_from_spec(release.kind, release.spec) if release is not None else None
        )

        open_statuses = {
            DeploymentDeviceStatus.PENDING.value,
            DeploymentDeviceStatus.QUEUED.value,
            DeploymentDeviceStatus.UPDATING.value,
        }
        # Only activated rows had desired rewritten; queued devices keep prior desired.
        undo_desired_statuses = {
            DeploymentDeviceStatus.PENDING.value,
            DeploymentDeviceStatus.UPDATING.value,
        }
        for row in self.deployments.list_devices(deployment_id=deployment.id):
            if row.status not in open_statuses:
                continue
            should_undo = row.status in undo_desired_statuses
            row.status = DeploymentDeviceStatus.CANCELLED.value
            if not should_undo or workload_key is None:
                continue
            if row.previous_desired_release_id is not None:
                previous = self.releases.get(
                    organization_id=organization_id,
                    release_id=row.previous_desired_release_id,
                )
                if previous is not None:
                    restored = self.software.set_desired_software(
                        organization_id=organization_id,
                        device_id=row.device_id,
                        release=previous,
                        commit=False,
                    )
                    self._notify_sync_desired(
                        device_id=row.device_id, revision=restored.desired_revision
                    )
                    continue
            cleared = self.software.clear_desired_software(
                organization_id=organization_id,
                device_id=row.device_id,
                workload_key=workload_key,
                commit=False,
            )
            if cleared is not None:
                self._notify_sync_desired(
                    device_id=row.device_id, revision=cleared.desired_revision
                )

        deployment.status = DeploymentStatus.CANCELLED.value
        self.session.flush()

    def retry_failed(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
        deployment = self.deployments.get(organization_id=organization_id, deployment_id=deployment_id)
        if deployment is None:
            raise NotFoundError("deployment_not_found", "Deployment was not found.")
        release = self.releases.get(organization_id=organization_id, release_id=deployment.release_id)
        if release is None:
            raise NotFoundError("release_not_found", "Release was not found.")

        failed_rows = [
            row
            for row in self.deployments.list_devices(deployment_id=deployment.id)
            if row.status == DeploymentDeviceStatus.FAILED.value
        ]
        if not failed_rows:
            raise AppError(
                "no_failed_devices",
                "This deployment has no failed devices to retry.",
                status_code=422,
            )

        for row in failed_rows:
            software = self.software.set_desired_software(
                organization_id=organization_id,
                device_id=row.device_id,
                release=release,
                commit=False,
            )
            row.status = DeploymentDeviceStatus.PENDING.value
            row.last_error = None
            self._notify_sync_desired(device_id=row.device_id, revision=software.desired_revision)

        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            deployment.status = DeploymentStatus.IN_PROGRESS.value
        self.session.flush()

    def rollback(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> str:
        # Rollback creates a new MeteorCloud Deploy (see DeploymentService.rollback_deployment).
        del organization_id, deployment_id
        raise AppError(
            "deployment_rollback_via_service",
            "Rollback is handled by the Deploy API, not the provider execute path.",
            status_code=501,
        )

    def get_deployment(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> DeploymentSnapshot:
        deployment = self.deployments.get(organization_id=organization_id, deployment_id=deployment_id)
        if deployment is None:
            raise NotFoundError("deployment_not_found", "Deployment was not found.")
        return DeploymentSnapshot(id=str(deployment.id), status=deployment.status)

    def list_device_software(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[DeviceSoftwareView]:
        rows = self.software.software.list_for_device(device_id=device_id)
        return [
            DeviceSoftwareView(
                workload_key=row.workload_key,
                desired_release_id=row.desired_release_id,
                installed_release_id=row.installed_release_id,
                sync_status=row.sync_status,
                health=row.health,
                metadata={
                    "kind": row.kind if isinstance(row.kind, str) else row.kind.value,
                    "desired_revision": row.desired_revision,
                    "installed_version": row.installed_version,
                },
            )
            for row in rows
            if row.organization_id == organization_id
        ]

    def apply_software_report(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID, report: dict[str, Any]
    ) -> None:
        item = SoftwareReportItem.model_validate(report)
        self.software.apply_software_reports(
            organization_id=organization_id,
            device_id=device_id,
            reports=[item],
        )

    def _notify_sync_desired(self, *, device_id: uuid.UUID, revision: int) -> None:
        payload = json.dumps({"type": "sync_desired", "revision": revision})
        try:
            self._publisher_or_default().publish(
                f"devices/{device_id}/commands",
                payload,
                qos=1,
                retain=False,
            )
        except Exception:
            logger.debug("MQTT sync_desired notify skipped for device %s", device_id, exc_info=True)
