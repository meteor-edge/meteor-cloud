"""Deploy action: targeting, history, cancel, progressive rollout, retry, rollback."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

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
from app.deployments.rollout import (
    PAUSE_MANUAL,
    RolloutConfig,
    RolloutRow,
    config_from_rollout,
    plan_tick,
)
from app.deployments.schemas import (
    DeploymentCounts,
    DeploymentCreate,
    DeploymentDeviceResponse,
    DeploymentResponse,
    DeployRollout,
    DeployTarget,
    EndpointSpec,
    ReleaseBrief,
)
from app.deployments.software_models import SoftwareHealth, SoftwareSyncStatus
from app.deployments.software_repository import DeviceSoftwareRepository
from app.deployments.software_service import DeviceSoftwareService
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.devices.schemas import Page
from app.endpoints.models import ApplicationEndpoint
from app.identity.models import User
from app.ports.deployment import DeploymentProvider
from app.releases.kinds import ReleaseKind, workload_key_from_spec
from app.releases.models import Release
from app.releases.repository import ReleaseRepository
from app.tenancy.models import OrganizationMembership

_CANCELLABLE_STATUSES = frozenset({DeploymentStatus.IN_PROGRESS.value, DeploymentStatus.PAUSED.value})

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
            timed_out=raw.get(DeploymentDeviceStatus.TIMED_OUT.value, 0),
        )
        counts.total = (
            counts.pending
            + counts.queued
            + counts.updating
            + counts.succeeded
            + counts.failed
            + counts.cancelled
            + counts.skipped
            + counts.timed_out
        )
        return counts

    def _release_brief(self, release: Release) -> ReleaseBrief:
        return ReleaseBrief(
            id=release.id,
            name=release.name,
            version=release.version,
            kind=release.kind,
        )

    def _device_map(self, device_ids: list[uuid.UUID]) -> dict[uuid.UUID, Device]:
        if not device_ids:
            return {}
        rows = self.session.scalars(select(Device).where(Device.id.in_(device_ids))).all()
        return {row.id: row for row in rows}

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
            devices = self._device_map([row.device_id for row in rows])
            workload_key = (
                workload_key_from_spec(release.kind, release.spec) if release is not None else None
            )
            endpoint_availability = {
                endpoint.device_id: endpoint.availability
                for endpoint in self.session.scalars(
                    select(ApplicationEndpoint).where(ApplicationEndpoint.deployment_id == deployment.id)
                )
            }
            software_rows = (
                self.software_rows.by_device_for_workload(
                    device_ids=[row.device_id for row in rows], workload_key=workload_key
                )
                if workload_key is not None
                else {}
            )
            devices_payload = []
            for row in rows:
                software_row = software_rows.get(row.device_id)
                device = devices.get(row.device_id)
                devices_payload.append(
                    DeploymentDeviceResponse(
                        id=row.id,
                        deployment_id=row.deployment_id,
                        device_id=row.device_id,
                        device_name=device.name if device else None,
                        device_last_seen_at=device.last_seen_at if device else None,
                        status=row.status,
                        progress_percent=software_row.progress_percent if software_row else None,
                        health=software_row.health if software_row else None,
                        reported_at=software_row.reported_at if software_row else None,
                        endpoint_availability=endpoint_availability.get(row.device_id),
                        activated_at=row.activated_at,
                        previous_desired_release_id=row.previous_desired_release_id,
                        # A crash after install is reported on the software row, not this one.
                        last_error=(
                            software_row.last_error
                            if software_row and software_row.health == "unhealthy" and software_row.last_error
                            else row.last_error
                        ),
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
        rollout = payload.rollout.to_record()
        progressive = rollout.get("strategy") in {"batched", "phased"}
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
                # Progressive rollouts start queued; the engine picks the first wave below.
                status = (
                    DeploymentDeviceStatus.QUEUED.value
                    if progressive
                    else DeploymentDeviceStatus.PENDING.value
                )
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

        now = datetime.now(UTC)
        rows = self.deployments.list_devices(deployment_id=deployment.id)
        state: dict[str, Any] | None = None
        config = config_from_rollout(rollout, len(deploy_ids)) if progressive else None
        if config is not None:
            # Target order, so batched keeps picking the first batch_size devices.
            position = {device_id: index for index, device_id in enumerate(all_device_ids)}
            ordered = sorted(rows, key=lambda row: position.get(row.device_id, len(position)))
            plan = plan_tick(self._rollout_rows(deployment, ordered, config), {}, config, now)
            activate_ids = plan.ids_to_activate
            state = plan.new_state
        else:
            activate_ids = list(deploy_ids)
        activate_set = set(activate_ids)
        for row in rows:
            if row.device_id in activate_set:
                row.status = DeploymentDeviceStatus.PENDING.value
                row.activated_at = now
        rollout = {
            **rollout,
            "activated_device_ids": [str(value) for value in activate_ids],
            "batch_released_at": now.isoformat(),
        }
        if state is not None and rollout.get("strategy") == "phased":
            rollout["state"] = state
        deployment.rollout = rollout
        flag_modified(deployment, "rollout")
        self.session.flush()

        if payload.endpoint is not None:
            if len(deploy_ids) != 1:
                raise ValidationAppError(
                    "endpoint_requires_one_device",
                    "A public URL can only be attached when the Deploy targets exactly one device.",
                )
            self._attach_endpoint(
                actor=actor,
                organization_id=organization_id,
                deployment_id=deployment.id,
                device_id=deploy_ids[0],
                release=release,
                spec=payload.endpoint,
            )

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

    def _published_container_ports(self, spec: dict[str, Any]) -> list[int]:
        ports: list[int] = []
        for item in spec.get("ports") or []:
            right = str(item).rsplit(":", 1)[-1]
            number = right.split("/", 1)[0]
            try:
                ports.append(int(number))
            except ValueError:
                continue
        return ports

    def _attach_endpoint(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
        device_id: uuid.UUID,
        release: Release,
        spec: EndpointSpec,
    ) -> None:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "endpoint.create")
        kind_value = release.kind.value if hasattr(release.kind, "value") else str(release.kind)
        published = self._published_container_ports(dict(release.spec or {}))
        if kind_value == ReleaseKind.DOCKER.value and published and spec.port not in published:
            raise ValidationAppError(
                "endpoint_port_mismatch",
                "Endpoint port must match a published container port on this docker release.",
            )
        from app.endpoints.schemas import EndpointCreate
        from app.endpoints.service import EndpointService

        endpoints = EndpointService(self.session)
        hostname = spec.hostname or endpoints.suggest_hostname(
            actor=actor, organization_id=organization_id, device_id=device_id, app=release.name
        ).hostname
        endpoints.create_endpoint(
            actor=actor,
            organization_id=organization_id,
            payload=EndpointCreate(
                device_id=device_id,
                hostname=hostname,
                port=spec.port,
                protocol=spec.protocol,
                deployment_id=deployment_id,
                workload_key=workload_key_from_spec(release.kind, release.spec),
            ),
            commit=False,
        )

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
        if deployment.status not in _CANCELLABLE_STATUSES:
            raise ConflictError(
                "deployment_not_cancellable",
                "Only in-progress or paused deployments can be cancelled.",
            )
        provider.cancel(organization_id=organization_id, deployment_id=deployment_id)
        self.session.refresh(deployment)
        if deployment.status in _CANCELLABLE_STATUSES:
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

    def pause_deployment(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> DeploymentResponse:
        """Stop activating new devices; activated devices keep installing and reporting."""
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.cancel")
        self._provider_or_404()
        self._require_deployment(organization_id, deployment_id)
        deployment = self._lock_deployment(deployment_id)
        assert deployment is not None
        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            raise ConflictError(
                "deployment_not_pausable",
                "Only in-progress deployments can be paused.",
            )
        if (deployment.rollout or {}).get("strategy") not in {"batched", "phased"}:
            raise ConflictError(
                "deployment_not_pausable",
                "Only batched or phased deployments can be paused.",
            )
        rollout = dict(deployment.rollout or {})
        rollout["state"] = {
            **(rollout.get("state") or {}),
            "paused_reason": PAUSE_MANUAL,
            "paused_at": datetime.now(UTC).isoformat(),
        }
        deployment.rollout = rollout
        flag_modified(deployment, "rollout")
        deployment.status = DeploymentStatus.PAUSED.value
        self.session.flush()
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="deployment.pause",
            resource_type="deployment",
            resource_id=deployment.id,
            metadata={"reason": PAUSE_MANUAL},
        )
        self.session.commit()
        return self._to_response(deployment, include_devices=True)

    def resume_deployment(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> DeploymentResponse:
        """Clear the pause and tick at once; the failure threshold is checked again."""
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "deployment.create")
        self._provider_or_404()
        self._require_deployment(organization_id, deployment_id)
        deployment = self._lock_deployment(deployment_id)
        assert deployment is not None
        if deployment.status != DeploymentStatus.PAUSED.value:
            raise ConflictError(
                "deployment_not_paused",
                "Only paused deployments can be resumed.",
            )
        rollout = dict(deployment.rollout or {})
        state = dict(rollout.get("state") or {})
        reason = state.pop("paused_reason", None)
        state.pop("paused_at", None)
        rollout["state"] = state
        deployment.rollout = rollout
        flag_modified(deployment, "rollout")
        deployment.status = DeploymentStatus.IN_PROGRESS.value
        deployment = self.tick_deployment(deployment)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="deployment.resume",
            resource_type="deployment",
            resource_id=deployment.id,
            metadata={"reason": reason, "status": deployment.status},
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
        # Paused is allowed: activated devices may already run the bad release.
        if original.status == DeploymentStatus.IN_PROGRESS.value:
            raise ConflictError(
                "deployment_still_in_progress",
                "Cancel, pause, or wait for the deployment to finish before rolling back.",
            )

        # Only devices that received this release; queued devices never changed.
        activated_rows = [
            row
            for row in self.deployments.list_devices(deployment_id=original.id)
            if row.activated_at is not None and row.status != DeploymentDeviceStatus.SKIPPED.value
        ]
        previous_ids = [
            row.previous_desired_release_id
            for row in activated_rows
            if row.previous_desired_release_id is not None
        ]
        if not previous_ids:
            raise ValidationAppError(
                "no_previous_release",
                "No previous desired release is recorded for this deployment.",
            )
        previous_release_id = Counter(previous_ids).most_common(1)[0][0]
        previous = self._require_release(organization_id, previous_release_id)

        payload = DeploymentCreate(
            release_id=previous.id,
            name=f"Rollback of {original.name or str(original.id)[:8]}",
            target=DeployTarget(device_ids=[row.device_id for row in activated_rows]),
            rollout=DeployRollout(strategy="immediate"),
        )
        return self.create_deployment(
            actor=actor,
            organization_id=organization_id,
            payload=payload,
            rollback_of_deployment_id=original.id,
        )

    def _lock_deployment(self, deployment_id: uuid.UUID) -> Deployment | None:
        """Re-load the row with SELECT ... FOR UPDATE so concurrent ticks serialize."""
        return self.session.scalar(
            select(Deployment)
            .where(Deployment.id == deployment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def tick_deployment(self, deployment: Deployment) -> Deployment:
        """Advance the rollout and refresh status under a row lock (API replicas race)."""
        self.session.flush()
        locked = self._lock_deployment(deployment.id)
        if locked is None or locked.status != DeploymentStatus.IN_PROGRESS.value:
            return locked or deployment
        self._advance_rollout(locked)
        self.refresh_deployment_status(locked)
        self.session.flush()
        return locked

    def refresh_deployment_status(self, deployment: Deployment) -> None:
        """Mark deployment completed/failed when every device is terminal."""
        if deployment.status != DeploymentStatus.IN_PROGRESS.value:
            return
        counts = self._counts(deployment.id)
        open_count = counts.pending + counts.queued + counts.updating
        if open_count > 0:
            return
        # timed_out counts like failed: the device never confirmed the release.
        failed = counts.failed + counts.timed_out
        if failed > 0 and counts.succeeded == 0 and counts.cancelled == 0 and counts.skipped == 0:
            deployment.status = DeploymentStatus.FAILED.value
        elif counts.cancelled > 0 and counts.succeeded == 0 and failed == 0:
            deployment.status = DeploymentStatus.CANCELLED.value
        else:
            # Succeeded and/or skipped (and no open rows) counts as completed.
            deployment.status = DeploymentStatus.COMPLETED.value

    def _rollout_rows(
        self, deployment: Deployment, rows: list[DeploymentDevice], config: RolloutConfig
    ) -> list[RolloutRow]:
        """Engine view of the rows: device last_seen_at and current health when needed."""
        last_seen: dict[uuid.UUID, datetime | None] = {}
        if config.order_by_last_seen:
            last_seen = {
                device_id: device.last_seen_at
                for device_id, device in self._device_map([row.device_id for row in rows]).items()
            }
        unhealthy: set[uuid.UUID] = set()
        if config.failure_threshold_percent is not None:
            release = self.releases.get(
                organization_id=deployment.organization_id, release_id=deployment.release_id
            )
            if release is not None:
                software = self.software_rows.by_device_for_workload(
                    device_ids=[
                        row.device_id
                        for row in rows
                        if row.status == DeploymentDeviceStatus.SUCCEEDED.value
                    ],
                    workload_key=workload_key_from_spec(release.kind, release.spec),
                )
                unhealthy = {
                    device_id
                    for device_id, item in software.items()
                    if item.health == SoftwareHealth.UNHEALTHY.value
                    and item.desired_release_id == deployment.release_id
                }
        return [
            RolloutRow(
                device_id=row.device_id,
                status=row.status,
                activated_at=row.activated_at,
                last_seen_at=last_seen.get(row.device_id),
                unhealthy=row.device_id in unhealthy,
            )
            for row in rows
        ]

    def _advance_rollout(self, deployment: Deployment) -> None:
        """One engine step for batched/phased Deploys: timeouts, pause, next activations."""
        rollout = dict(deployment.rollout or {})
        strategy = rollout.get("strategy")
        rows = self.deployments.list_devices(deployment_id=deployment.id)
        total = sum(1 for row in rows if row.status != DeploymentDeviceStatus.SKIPPED.value)
        config = config_from_rollout(rollout, total)
        if config is None:
            return
        state = dict(rollout.get("state") or {})
        if strategy == "batched":
            # Batched keeps no state; its delay counts from the last batch release.
            state = {"phase_started_at": rollout.get("batch_released_at")}
        now = datetime.now(UTC)
        plan = plan_tick(self._rollout_rows(deployment, rows, config), state, config, now)

        by_device = {row.device_id: row for row in rows}
        for device_id in plan.ids_to_time_out:
            row = by_device[device_id]
            row.status = DeploymentDeviceStatus.TIMED_OUT.value
            row.last_error = (
                f"No report within {config.pending_timeout_seconds} seconds of activation."
            )
        if plan.pause_reason is not None:
            deployment.status = DeploymentStatus.PAUSED.value

        if plan.ids_to_activate:
            try:
                provider = self._provider_or_404()
            except NotFoundError:
                self.session.flush()
                return
            for device_id in plan.ids_to_activate:
                row = by_device[device_id]
                row.status = DeploymentDeviceStatus.PENDING.value
                row.last_error = None
                row.activated_at = now
            provider.deploy(
                organization_id=deployment.organization_id,
                deployment_id=deployment.id,
                release_id=deployment.release_id,
                device_ids=list(plan.ids_to_activate),
                rollout=rollout,
                created_by_user_id=deployment.created_by_user_id or uuid.UUID(int=0),
            )
            rollout["activated_device_ids"] = sorted(
                str(row.device_id) for row in rows if row.activated_at is not None
            )
            rollout["batch_released_at"] = now.isoformat()
        if strategy == "phased":
            rollout["state"] = plan.new_state
        if rollout != deployment.rollout:
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
        # Lock touched Deploys in id order before writing their rows, so two
        # heartbeats (or a heartbeat and a tick) cannot deadlock on each other.
        for deployment_id in sorted({progress.deployment_id for progress in open_rows}):
            self._lock_deployment(deployment_id)
        touched_deployments: dict[uuid.UUID, Deployment] = {}
        for progress in open_rows:
            deployment = self.deployments.get(
                organization_id=organization_id, deployment_id=progress.deployment_id
            )
            status = mapped
            if (
                deployment is not None
                and (deployment.rollout or {}).get("strategy") == "phased"
                and row.sync_status == SoftwareSyncStatus.IN_SYNC.value
                and row.health == SoftwareHealth.UNHEALTHY.value
            ):
                # Installed but stopped or crashing: a failure for the phased budget.
                status = DeploymentDeviceStatus.FAILED.value
            if progress.status == DeploymentDeviceStatus.TIMED_OUT.value and status not in {
                DeploymentDeviceStatus.SUCCEEDED.value,
                DeploymentDeviceStatus.FAILED.value,
            }:
                # A late device may still finish, but never goes back to pending.
                continue
            progress.status = status
            progress.last_error = row.last_error
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
        for deployment_id in sorted(touched_deployments):
            self.tick_deployment(touched_deployments[deployment_id])
