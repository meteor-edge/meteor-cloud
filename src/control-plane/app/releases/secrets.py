"""Environment values a release keeps encrypted: never in its spec, the API or the audit log."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.secretbox import SecretBoxError, seal, unseal
from app.deployments.models import Deployment, DeploymentDevice, DeploymentDeviceStatus
from app.deployments.software_models import DeviceSoftware, SoftwareSyncStatus
from app.devices.models import Device
from app.releases.models import Release, ReleaseSecret


def _context(release_id: uuid.UUID, name: str) -> str:
    return f"release_secrets:{release_id}:{name}"


class ReleaseSecrets:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def store(self, release_id: uuid.UUID, values: dict[str, str]) -> None:
        for name, value in values.items():
            sealed = seal(self.settings, value, context=_context(release_id, name))
            self.session.add(ReleaseSecret(release_id=release_id, name=name, sealed_value=sealed))
        self.session.flush()

    def values(self, release_id: uuid.UUID) -> dict[str, str]:
        try:
            return {
                row.name: unseal(self.settings, row.sealed_value, context=_context(release_id, row.name))
                for row in self.session.scalars(select(ReleaseSecret).where(ReleaseSecret.release_id == release_id))
            }
        except SecretBoxError as exc:
            raise AppError(
                "secrets_unreadable",
                "The secrets of this release cannot be decrypted (was the encryption key changed?). Set them again.",
                status_code=409,
            ) from exc

    def for_device(self, *, device: Device, workload_key: str, release_id: uuid.UUID) -> dict[str, str]:
        """Only for a release an operator put on this device: the desired one, or one it ran (a rollback)."""
        release = self.session.get(Release, release_id)
        if release is None or release.organization_id != device.organization_id:
            raise NotFoundError("release_not_found", "Release was not found.")
        row = self.session.scalar(
            select(DeviceSoftware).where(
                DeviceSoftware.device_id == device.id, DeviceSoftware.workload_key == workload_key
            )
        )
        if (
            row is None
            or row.desired_release_id is None
            or row.sync_status == SoftwareSyncStatus.CANCELLED.value
            or (row.desired_release_id != release_id and not self._ran(device, release_id))
            or str((release.spec or {}).get("container_name")) != workload_key
        ):
            raise NotFoundError("release_not_found", "Release was not found.")
        return self.values(release_id)

    def _ran(self, device: Device, release_id: uuid.UUID) -> bool:
        return (
            self.session.scalar(
                select(DeploymentDevice.id)
                .join(Deployment, Deployment.id == DeploymentDevice.deployment_id)
                .where(
                    DeploymentDevice.device_id == device.id,
                    DeploymentDevice.status == DeploymentDeviceStatus.SUCCEEDED.value,
                    Deployment.release_id == release_id,
                )
                .limit(1)
            )
            is not None
        )
