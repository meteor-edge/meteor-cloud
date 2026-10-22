"""Deploy action: targeting, history, cancel, progressive rollout, retry, rollback."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.adapters import deployment_provider as resolve_deployment_provider
from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.deployments.models import (
    Deployment,
    DeploymentDevice,
    DeploymentDeviceStatus,
    DeploymentStatus,
)
from app.deployments.repository import DeploymentRepository
from app.deployments.schemas import (
    DeploymentCounts,
    DeploymentCreate,
    DeploymentDeviceResponse,
    DeploymentResponse,
    DeployRollout,
    DeployTarget,
    ReleaseBrief,
)
from app.deployments.software_models import SoftwareSyncStatus
from app.deployments.software_repository import DeviceSoftwareRepository
from app.deployments.software_service import DeviceSoftwareService
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.devices.schemas import Page
from app.identity.models import User
from app.ports.deployment import DeploymentProvider
from app.releases.kinds import ReleaseKind, workload_key_from_spec
from app.releases.models import Release
from app.releases.repository import ReleaseRepository
from app.tenancy.models import OrganizationMembership

# Capability flag required for each release kind (when the device has reported caps).
_KIND_CAPABILITY: dict[str, str] = {
    ReleaseKind.SYSTEMD.value: "systemd",
    ReleaseKind.DOCKER.value: "docker",
    ReleaseKind.DOCKER_COMPOSE.value: "docker_compose",
}


class DeploymentService:
    def __init__(
        self,
        session: Session,
        *,
        provider: DeploymentProvider | None = None,
        provider_name: str | None = None,
        resolve_provider: bool = True,
    ) -> None:
        self.session = session
        self.deployments = DeploymentRepository(session)
        self.devices = DeviceRepository(session)
        self.releases = ReleaseRepository(session)
        self.software = DeviceSoftwareService(session)
        self.software_rows = DeviceSoftwareRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)
        self._provider = provider
        self._provider_name = provider_name or get_settings().deployment_provider
        # When False, ``provider=None`` means deployments are disabled (DI path).
        self._resolve_provider = resolve_provider

    def _provider_or_404(self) -> DeploymentProvider:
        if not self._resolve_provider:
            if self._provider is None:
                raise NotFoundError(
                    "deployments_disabled",
                    "Deployments are disabled for this deployment.",
                )
            return self._provider
        if self._provider is not None:
            return self._provider
        provider = resolve_deployment_provider(get_settings(), self.session)
        if provider is None:
            raise NotFoundError(
                "deployments_disabled",
                "Deployments are disabled for this deployment.",
            )
        return provider

    def _require_membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        return self.authz.require_membership(user_id=user_id, organization_id=organization_id)

    def _require_release(self, organization_id: uuid.UUID, release_id: uuid.UUID) -> Release:
        release = self.releases.get(organization_id=organization_id, release_id=release_id)
        if release is None:
            raise NotFoundError("release_not_found", "Release was not found.")
        return release

    def _require_deployment(self, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> Deployment:
        deployment = self.deployments.get(organization_id=organization_id, deployment_id=deployment_id)
        if deployment is None:
            raise NotFoundError("deployment_not_found", "Deployment was not found.")
        return deployment

    def device_supports_kind(self, device: Device, kind: ReleaseKind | str) -> bool:
        """False only when the device has reported capabilities and lacks the kind's runtime."""
        kind_value = kind.value if isinstance(kind, ReleaseKind) else str(kind)
        required = _KIND_CAPABILITY.get(kind_value)
        if required is None:
            return True
        caps = (device.metadata_ or {}).get("capabilities")
        if not isinstance(caps, dict) or not caps:
            return True
        if required not in caps:
            return True
        return bool(caps.get(required))

    def expand_targets(
        self,
        *,
        membership: OrganizationMembership,
        organization_id: uuid.UUID,
        device_ids: list[uuid.UUID],
        group_ids: list[uuid.UUID],
        device_type_ids: list[uuid.UUID],
    ) -> list[Device]:
        """Resolve target selectors to enabled, in-scope devices (deduped)."""
        collected: dict[uuid.UUID, Device] = {}

        for device_id in device_ids:
            device = self.devices.get(organization_id=organization_id, device_id=device_id)
            if device is None:
                raise NotFoundError("device_not_found", f"Device {device_id} was not found.")
            if not self.authz.allows_device_group(membership, device.device_group_id):
                raise NotFoundError("device_not_found", f"Device {device_id} was not found.")
            if not device.is_enabled:
                continue
            collected[device.id] = device

        for group_id in group_ids:
            if not self.authz.allows_device_group(membership, group_id):
                continue
            for device in self.session.scalars(
                select(Device).where(
                    Device.organization_id == organization_id,
                    Device.device_group_id == group_id,
                    Device.is_enabled.is_(True),
                )
            ).all():
                if self.authz.allows_device_group(membership, device.device_group_id):
                    collected[device.id] = device

        for type_id in device_type_ids:
            for device in self.session.scalars(
                select(Device).where(
                    Device.organization_id == organization_id,
                    Device.device_type_id == type_id,
                    Device.is_enabled.is_(True),
                )
            ).all():
                if self.authz.allows_device_group(membership, device.device_group_id):
                    collected[device.id] = device

        if not collected:
            raise ValidationAppError(
                "no_deploy_targets",
                "No enabled devices matched the selected targets.",
            )
        return list(collected.values())

    def _counts(self, deployment_id: uuid.UUID) -> DeploymentCounts:
        raw = self.deployments.count_by_status(deployment_id=deployment_id)
        counts = DeploymentCounts(
            pending=raw.get(DeploymentDeviceStatus.PENDING.value, 0),
            queued=raw.get(DeploymentDeviceStatus.QUEUED.value, 0),
            updating=raw.get(DeploymentDeviceStatus.UPDATING.value, 0),
            succeeded=raw.get(DeploymentDeviceStatus.SUCCEEDED.value, 0),
            failed=raw.get(DeploymentDeviceStatus.FAILED.value, 0),
            cancelled=raw.get(DeploymentDeviceStatus.CANCELLED.value, 0),
            skipped=raw.get(DeploymentDeviceStatus.SKIPPED.value, 0),
        )
        counts.total = (
            counts.pending
            + counts.queued
            + counts.updating
            + counts.succeeded
            + counts.failed
            + counts.cancelled
            + counts.skipped
        )
        return counts

    def _release_brief(self, release: Release) -> ReleaseBrief:
        return ReleaseBrief(
            id=release.id,
            name=release.name,
            version=release.version,
            kind=release.kind,
        )

    def _device_name_map(self, device_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not device_ids:
            return {}
        rows = self.session.scalars(select(Device).where(Device.id.in_(device_ids))).all()
        return {row.id: row.name for row in rows}

    def _to_response(
        self,
        deployment: Deployment,
        *,
        include_devices: bool = False,
    ) -> DeploymentResponse:
        release = self.releases.get(
            organization_id=deployment.organization_id, release_id=deployment.release_id
        )
        devices_payload: list[DeploymentDeviceResponse] | None = None
        if include_devices:
            rows = self.deployments.list_devices(deployment_id=deployment.id)
            names = self._device_name_map([row.device_id for row in rows])
            workload_key = (
                workload_key_from_spec(release.kind, release.spec) if release is not None else None
            )
            devices_payload = []
            for row in rows:
                progress: int | None = None
                if workload_key is not None:
                    software_row = self.software_rows.get_by_workload(
                        device_id=row.device_id, workload_key=workload_key
                    )
                    if software_row is not None:
                        progress = software_row.progress_percent
                devices_payload.append(
                    DeploymentDeviceResponse(
                        id=row.id,
                        deployment_id=row.deployment_id,
                        device_id=row.device_id,
                        device_name=names.get(row.device_id),
                        status=row.status,
                        progress_percent=progress,
                        previous_desired_release_id=row.previous_desired_release_id,
                        last_error=row.last_error,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    )
                )
        return DeploymentResponse(
            id=deployment.id,
            organization_id=deployment.organization_id,
            release_id=deployment.release_id,
            release=self._release_brief(release) if release else None,
            name=deployment.name,
            target=dict(deployment.target or {}),
            rollout=dict(deployment.rollout or {}),
            status=deployment.status,
            provider=deployment.provider,
            provider_ref=deployment.provider_ref,
            counts=self._counts(deployment.id),
            created_by_user_id=deployment.created_by_user_id,
            rollback_of_deployment_id=deployment.rollback_of_deployment_id,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
            devices=devices_payload,
        )

    def create_deployment(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: DeploymentCreate,
        rollback_of_deployment_id: uuid.UUID | None = None,
    ) -> DeploymentResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.create")
        provider = self._provider_or_404()

        release = self._require_release(organization_id, payload.release_id)
        devices = self.expand_targets(
            membership=membership,
            organization_id=organization_id,
            device_ids=payload.target.device_ids,
            group_ids=payload.target.group_ids,
            device_type_ids=payload.target.device_type_ids,
        )
        all_device_ids = [device.id for device in devices]
        rollout = payload.rollout.model_dump()
        workload_key = workload_key_from_spec(release.kind, release.spec)
        kind_value = release.kind.value if hasattr(release.kind, "value") else str(release.kind)
        cap_name = _KIND_CAPABILITY.get(kind_value, kind_value)

        # MeteorCloud always records Deploy history; the provider only executes it.
        deployment = Deployment(
            organization_id=organization_id,
            release_id=release.id,
            name=payload.name,
            target={
                "device_ids": [str(value) for value in payload.target.device_ids],
                "group_ids": [str(value) for value in payload.target.group_ids],
                "device_type_ids": [str(value) for value in payload.target.device_type_ids],
                "resolved_device_ids": [str(value) for value in all_device_ids],
            },
            rollout=rollout,
            status=DeploymentStatus.IN_PROGRESS.value,
            provider=self._provider_name if self._provider_name != "none" else "native",
            created_by_user_id=actor.id,
            rollback_of_deployment_id=rollback_of_deployment_id,
        )
        self.deployments.create(deployment)

        deploy_ids: list[uuid.UUID] = []
        for device in devices:
            existing = self.software_rows.get_by_workload(
                device_id=device.id, workload_key=workload_key
            )
            previous_desired = existing.desired_release_id if existing else None
            if self.device_supports_kind(device, release.kind):
                status = DeploymentDeviceStatus.PENDING.value
                last_error = None
                deploy_ids.append(device.id)
            else:
                status = DeploymentDeviceStatus.SKIPPED.value
                last_error = f"Device lacks {cap_name} capability."
            self.deployments.create_device(
                DeploymentDevice(
                    deployment_id=deployment.id,
                    device_id=device.id,
                    status=status,
                    previous_desired_release_id=previous_desired,
                    last_error=last_error,
                )
            )
        self.session.flush()

        activate_ids = self._initial_batch(deploy_ids, rollout)
        activate_set = set(activate_ids)
        if rollout.get("strategy") == "batched":
            for row in self.deployments.list_devices(deployment_id=deployment.id):
                if (
                    row.status == DeploymentDeviceStatus.PENDING.value
                    and row.device_id not in activate_set
                ):
                    row.status = DeploymentDeviceStatus.QUEUED.value
        rollout = {
            **rollout,
            "activated_device_ids": [str(value) for value in activate_ids],
            "batch_released_at": datetime.now(UTC).isoformat(),
        }
        deployment.rollout = rollout
        flag_modified(deployment, "rollout")
        self.session.flush()

        if activate_ids:
            provider_ref = provider.deploy(
                organization_id=organization_id,
                deployment_id=deployment.id,
                release_id=release.id,
                device_ids=activate_ids,
                rollout=rollout,
                created_by_user_id=actor.id,
            )
            deployment.provider_ref = provider_ref or None
        elif not deploy_ids:
            deployment.status = DeploymentStatus.COMPLETED.value
            deployment.provider_ref = None
        self.session.commit()
        self.session.refresh(deployment)

        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="deployment.create",
            resource_type="deployment",
            resource_id=deployment.id,
            metadata={
                "release_id": str(release.id),
                "device_count": len(all_device_ids),
                "deployed_count": len(activate_ids),
                "queued_count": len(deploy_ids) - len(activate_ids),
                "skipped_count": len(all_device_ids) - len(deploy_ids),
                "name": deployment.name,
                "provider": deployment.provider,
                "provider_ref": deployment.provider_ref,
                "rollout": rollout.get("strategy"),
            },
        )
        self.session.commit()
        return self._to_response(deployment, include_devices=True)

    @staticmethod
    def _initial_batch(deploy_ids: list[uuid.UUID], rollout: dict) -> list[uuid.UUID]:
        if not deploy_ids:
            return []
        if rollout.get("strategy") != "batched":
            return list(deploy_ids)
        batch_size = int(rollout.get("batch_size") or len(deploy_ids))
        return list(deploy_ids[: max(1, batch_size)])

    def list_deployments(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        status: str | None = None,
        release_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[DeploymentResponse]:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.read")
        self._provider_or_404()
        items, total = self.deployments.list_paginated(
            organization_id=organization_id,
            status=status,
            release_id=release_id,
            page=page,
            page_size=page_size,
        )
        return Page[DeploymentResponse](
            items=[self._to_response(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_deployment(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> DeploymentResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.read")
        self._provider_or_404()
        deployment = self._require_deployment(organization_id, deployment_id)
        return self._to_response(deployment, include_devices=True)

    def cancel_deployment(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> DeploymentResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.cancel")
        provider = self._provider_or_404()
        deployment = self._require_deployment(organization_id, deployment_id)
        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            raise ConflictError(
                "deployment_not_cancellable",
                "Only in-progress deployments can be cancelled.",
            )
        provider.cancel(organization_id=organization_id, deployment_id=deployment_id)
        self.session.refresh(deployment)
        if deployment.status == DeploymentStatus.IN_PROGRESS.value:
            # Providers that only talk to a vendor still leave local history to us.
            open_statuses = {
                DeploymentDeviceStatus.PENDING.value,
                DeploymentDeviceStatus.QUEUED.value,
                DeploymentDeviceStatus.UPDATING.value,
            }
            for row in self.deployments.list_devices(deployment_id=deployment.id):
                if row.status in open_statuses:
                    row.status = DeploymentDeviceStatus.CANCELLED.value
            deployment.status = DeploymentStatus.CANCELLED.value
            self.session.flush()
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="deployment.cancel",
            resource_type="deployment",
            resource_id=deployment.id,
            metadata={"status": deployment.status},
        )
        self.session.commit()
        return self._to_response(deployment, include_devices=True)

    def retry_failed(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> DeploymentResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.create")
        provider = self._provider_or_404()
        deployment = self._require_deployment(organization_id, deployment_id)
        failed = [
            row
            for row in self.deployments.list_devices(deployment_id=deployment.id)
            if row.status == DeploymentDeviceStatus.FAILED.value
        ]
        if not failed:
            raise ValidationAppError(
                "no_failed_devices",
                "This deployment has no failed devices to retry.",
            )
        provider.retry_failed(organization_id=organization_id, deployment_id=deployment_id)
        self.session.refresh(deployment)
        if deployment.status in {
            DeploymentStatus.COMPLETED.value,
            DeploymentStatus.FAILED.value,
        }:
            deployment.status = DeploymentStatus.IN_PROGRESS.value
        self.session.flush()
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="deployment.retry_failed",
            resource_type="deployment",
            resource_id=deployment.id,
            metadata={"retried": len(failed)},
        )
        self.session.commit()
        return self._to_response(deployment, include_devices=True)

    def rollback_deployment(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> DeploymentResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.rollback")
        self._provider_or_404()
        original = self._require_deployment(organization_id, deployment_id)
        if original.status == DeploymentStatus.IN_PROGRESS.value:
            raise ConflictError(
                "deployment_still_in_progress",
                "Cancel or wait for the deployment to finish before rolling back.",
            )

        previous_ids = [
            row.previous_desired_release_id
            for row in self.deployments.list_devices(deployment_id=original.id)
            if row.previous_desired_release_id is not None
            and row.status != DeploymentDeviceStatus.SKIPPED.value
        ]
        if not previous_ids:
            raise ValidationAppError(
                "no_previous_release",
                "No previous desired release is recorded for this deployment.",
            )
        previous_release_id = Counter(previous_ids).most_common(1)[0][0]
        previous = self._require_release(organization_id, previous_release_id)

        resolved = [
            uuid.UUID(value)
            for value in (original.target or {}).get("resolved_device_ids") or []
        ]
        if not resolved:
            resolved = [
                row.device_id
                for row in self.deployments.list_devices(deployment_id=original.id)
                if row.status != DeploymentDeviceStatus.SKIPPED.value
            ]

        payload = DeploymentCreate(
            release_id=previous.id,
            name=f"Rollback of {original.name or str(original.id)[:8]}",
            target=DeployTarget(device_ids=resolved),
            rollout=DeployRollout(strategy="immediate"),
        )
        return self.create_deployment(
            actor=actor,
            organization_id=organization_id,
            payload=payload,
            rollback_of_deployment_id=original.id,
        )

    def refresh_deployment_status(self, deployment: Deployment) -> None:
        """Mark deployment completed/failed when every device is terminal."""
        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            return
        self.maybe_advance_batched_rollout(deployment)
        counts = self._counts(deployment.id)
        open_count = counts.pending + counts.queued + counts.updating
        if open_count > 0:
            return
        if counts.failed > 0 and counts.succeeded == 0 and counts.cancelled == 0 and counts.skipped == 0:
            deployment.status = DeploymentStatus.FAILED.value
        elif counts.cancelled > 0 and counts.succeeded == 0 and counts.failed == 0:
            deployment.status = DeploymentStatus.CANCELLED.value
        else:
            # Succeeded and/or skipped (and no open rows) counts as completed.
            deployment.status = DeploymentStatus.COMPLETED.value

    def maybe_advance_batched_rollout(self, deployment: Deployment) -> None:
        """When the current batch is terminal, activate the next batch of devices."""
        rollout = dict(deployment.rollout or {})
        if rollout.get("strategy") != "batched":
            return
        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            return

        activated = {str(value) for value in (rollout.get("activated_device_ids") or [])}
        rows = self.deployments.list_devices(deployment_id=deployment.id)
        open_statuses = {
            DeploymentDeviceStatus.PENDING.value,
            DeploymentDeviceStatus.UPDATING.value,
        }
        open_activated = [
            row
            for row in rows
            if str(row.device_id) in activated and row.status in open_statuses
        ]
        if open_activated:
            return

        delay = int(rollout.get("delay_seconds") or 0)
        if delay > 0:
            released_raw = rollout.get("batch_released_at")
            if released_raw:
                try:
                    released_at = datetime.fromisoformat(str(released_raw))
                    if released_at.tzinfo is None:
                        released_at = released_at.replace(tzinfo=UTC)
                    if datetime.now(UTC) < released_at + timedelta(seconds=delay):
                        return
                except ValueError:
                    pass

        waiting = [
            row
            for row in rows
            if str(row.device_id) not in activated
            and row.status == DeploymentDeviceStatus.QUEUED.value
        ]
        if not waiting:
            return

        batch_size = int(rollout.get("batch_size") or 1)
        next_batch = waiting[: max(1, batch_size)]
        next_ids = [row.device_id for row in next_batch]
        if not next_ids:
            return

        try:
            provider = self._provider_or_404()
        except NotFoundError:
            return

        for row in next_batch:
            row.status = DeploymentDeviceStatus.PENDING.value
            row.last_error = None

        provider.deploy(
            organization_id=deployment.organization_id,
            deployment_id=deployment.id,
            release_id=deployment.release_id,
            device_ids=next_ids,
            rollout=rollout,
            created_by_user_id=deployment.created_by_user_id or uuid.UUID(int=0),
        )
        activated.update(str(device_id) for device_id in next_ids)
        rollout = {
            **rollout,
            "activated_device_ids": sorted(activated),
            "batch_released_at": datetime.now(UTC).isoformat(),
        }
        deployment.rollout = rollout
        flag_modified(deployment, "rollout")
        self.session.flush()

    def sync_progress_from_software(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        workload_key: str,
    ) -> None:
        """Update open deployment_devices from the current device_software row."""
        row = self.software_rows.get_by_workload(device_id=device_id, workload_key=workload_key)
        if row is None or row.organization_id != organization_id:
            return
        if row.desired_release_id is None:
            return

        status_map = {
            SoftwareSyncStatus.IN_SYNC.value: DeploymentDeviceStatus.SUCCEEDED.value,
            SoftwareSyncStatus.FAILED.value: DeploymentDeviceStatus.FAILED.value,
            SoftwareSyncStatus.UPDATING.value: DeploymentDeviceStatus.UPDATING.value,
            SoftwareSyncStatus.PENDING.value: DeploymentDeviceStatus.PENDING.value,
            SoftwareSyncStatus.CANCELLED.value: DeploymentDeviceStatus.CANCELLED.value,
        }
        mapped = status_map.get(row.sync_status)
        if mapped is None:
            return

        open_rows = self.deployments.list_active_for_device(
            organization_id=organization_id,
            device_id=device_id,
            release_id=row.desired_release_id,
        )
        touched_deployments: dict[uuid.UUID, Deployment] = {}
        for progress in open_rows:
            progress.status = mapped
            progress.last_error = row.last_error
            deployment = self.deployments.get(
                organization_id=organization_id, deployment_id=progress.deployment_id
            )
            if deployment is not None:
                touched_deployments[deployment.id] = deployment

        # Also refresh in-progress deployments that include this device so batched
        # rollouts can advance after delay_seconds even when this device has no
        # open rows left.
        for deployment in self.deployments.list_in_progress_for_device(
            organization_id=organization_id,
            device_id=device_id,
        ):
            touched_deployments.setdefault(deployment.id, deployment)

        # Flush so status aggregates see the updated deployment_devices rows.
        if open_rows:
            self.session.flush()
        for deployment in touched_deployments.values():
            self.refresh_deployment_status(deployment)
