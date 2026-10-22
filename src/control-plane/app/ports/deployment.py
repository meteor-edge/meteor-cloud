"""Deployment provider port.

MeteorCloud always owns the release catalog, Deploy API/UI, and local Deploy
history (``deployments`` / ``deployment_devices``).

``DeploymentProvider`` is only the *execution* engine:

* ``native`` — write desired software + MQTT wake-up (MeteorCloud agents)
* ``mender`` — create/cancel the equivalent deployment in Mender (adapter later)
* ``none`` — Deploy routes disabled

Releases stay in MeteorCloud regardless of provider. Switching
``DEPLOYMENT_PROVIDER`` changes where a Deploy is carried out, not whether
operators can define releases or open the Deployments UI.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class DeploymentSnapshot:
    """Opaque deployment summary returned by a provider."""

    def __init__(
        self,
        *,
        id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.status = status
        self.metadata = metadata or {}


class DeviceSoftwareView:
    """Desired vs installed software for one workload on a device."""

    def __init__(
        self,
        *,
        workload_key: str,
        desired_release_id: uuid.UUID | None = None,
        installed_release_id: uuid.UUID | None = None,
        sync_status: str = "pending",
        health: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.workload_key = workload_key
        self.desired_release_id = desired_release_id
        self.installed_release_id = installed_release_id
        self.sync_status = sync_status
        self.health = health
        self.metadata = metadata or {}


class DeploymentProvider(Protocol):
    """Execute a Deploy that MeteorCloud has already recorded.

    Implementations must not own the operator-facing release catalog or the
    Deployments list/detail UI. They receive a MeteorCloud ``deployment_id`` and
    may return a vendor-specific reference (stored as ``provider_ref``).
    """

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
        """Apply the release to the given devices.

        Return a provider-specific reference (empty string when none). The
        MeteorCloud deployment row already exists before this is called.
        """
        ...

    def cancel(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
        """Cancel an in-progress deploy in the provider (and undo local apply if native)."""
        ...

    def retry_failed(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
        """Re-queue failed devices for the same desired release."""
        ...

    def rollback(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> str:
        """Deploy the previous release; return a new provider_ref if any."""
        ...

    def get_deployment(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> DeploymentSnapshot:
        """Return provider-side status for the MeteorCloud deployment id."""
        ...

    def list_device_software(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[DeviceSoftwareView]:
        """Return desired vs installed software for a device."""
        ...

    def apply_software_report(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID, report: dict[str, Any]
    ) -> None:
        """Apply an agent software status report (native agents; vendors may no-op)."""
        ...
