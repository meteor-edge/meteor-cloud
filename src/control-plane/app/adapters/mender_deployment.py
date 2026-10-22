"""Mender deployment provider stub.

MeteorCloud still owns releases and Deploy history/UI. When
``DEPLOYMENT_PROVIDER=mender``, a Deploy would create the equivalent deployment
in Mender and store Mender's id as ``provider_ref``.

This adapter is intentionally unimplemented: selecting ``mender`` keeps the API
up (releases and list/detail work) but execute/cancel return 501 until a real
Mender client is added behind this port.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.ports.deployment import DeploymentSnapshot, DeviceSoftwareView

_NOT_IMPLEMENTED = AppError(
    "mender_not_implemented",
    "Mender deployment provider is not implemented yet. "
    "Releases and Deploy history stay in MeteorCloud; set DEPLOYMENT_PROVIDER=native "
    "to execute deploys locally, or implement app.adapters.mender_deployment.",
    status_code=501,
)


class MenderDeploymentProvider:
    """Reserved DeploymentProvider for Mender. Methods fail fast with 501."""

    def __init__(self, session: Session) -> None:
        self.session = session

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
        raise _NOT_IMPLEMENTED

    def cancel(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
        raise _NOT_IMPLEMENTED

    def retry_failed(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
        raise _NOT_IMPLEMENTED

    def rollback(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> str:
        raise _NOT_IMPLEMENTED

    def get_deployment(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> DeploymentSnapshot:
        raise _NOT_IMPLEMENTED

    def list_device_software(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[DeviceSoftwareView]:
        raise _NOT_IMPLEMENTED

    def apply_software_report(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID, report: dict[str, Any]
    ) -> None:
        raise _NOT_IMPLEMENTED
