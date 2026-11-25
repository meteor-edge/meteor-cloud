"""Run an app: a release made from an image plus a Deploy to one device, in one request."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.deployments.schemas import (
    DeploymentCreate,
    DeploymentResponse,
    DeployTarget,
    EndpointSpec,
)
from app.deployments.service import DeploymentService
from app.deployments.software_models import SoftwareSyncStatus
from app.identity.models import User
from app.releases.kinds import HealthCheck, ReleaseKind, docker_host_ports
from app.releases.models import Release
from app.releases.schemas import ReleaseCreate
from app.releases.service import ReleaseService

_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9_.-]{0,61}[a-z0-9])?")
_VERSION_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class AppEndpoint(BaseModel):
    # Empty: the suggested address.
    hostname: str | None = Field(default=None, max_length=255)
    # Empty: the first published port.
    port: int | None = Field(default=None, ge=1, le=65535)


class AppRunRequest(BaseModel):
    image: str = Field(min_length=1, max_length=512)
    # "meteorcloud": the organization's own registry. null: a public image.
    registry: Literal["meteorcloud"] | None = "meteorcloud"
    # Empty: the last part of the image's name.
    name: str | None = Field(default=None, max_length=63)
    ports: list[str] = Field(default_factory=lambda: ["8080:8080"], max_length=20)
    env: dict[str, str] = Field(default_factory=dict, max_length=100)
    command: str | None = Field(default=None, max_length=1024)
    health_check: HealthCheck | None = None
    endpoint: AppEndpoint | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower()
        if not value:
            return None
        if not _NAME_RE.fullmatch(value):
            raise ValueError("use lowercase letters, digits, '.', '-' and '_'")
        return value


class AppRunResponse(BaseModel):
    workload_key: str
    release_id: uuid.UUID
    deployment: DeploymentResponse


def _default_name(image: str) -> str:
    last = image.partition("@")[0].rsplit("/", 1)[-1].partition(":")[0]
    name = re.sub(r"[^a-z0-9_.-]+", "-", last.lower()).strip("-_.")[:63].strip("-_.")
    if not name:
        raise ValidationAppError("invalid_release_spec", f"Not an image reference: {image!r}.")
    return name


def _tag(image: str) -> str:
    """The release's version starts from the tag."""
    tag = image.partition("@")[0].rsplit("/", 1)[-1].partition(":")[2] or "latest"
    return _VERSION_CHARS.sub("-", tag)[:50].strip("._-") or "latest"


class AppService:
    def __init__(self, deployments: DeploymentService, releases: ReleaseService) -> None:
        self.session = deployments.session
        self.deployments = deployments
        self.releases = releases

    def run(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: AppRunRequest
    ) -> AppRunResponse:
        authz = self.deployments.authz
        membership = authz.require_membership(user_id=actor.id, organization_id=organization_id)
        device = self.deployments.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        authz.require(membership, "deployment.create", device_group_id=device.device_group_id)
        if payload.endpoint is not None:
            authz.require(membership, "endpoint.create")

        name = payload.name or _default_name(payload.image)
        existing = self.deployments.software_rows.get_by_workload(device_id=device.id, workload_key=name)
        if (
            existing is not None
            and existing.desired_release_id is not None
            and existing.sync_status != SoftwareSyncStatus.CANCELLED.value
        ):
            raise ConflictError("app_exists", f"This device already runs an app named {name}. Choose another name.")

        spec = {
            "container_name": name,
            "image": payload.image.strip(),
            "ports": payload.ports,
            "env": payload.env,
            "command": payload.command,
            "registry": payload.registry,
            "health_check": payload.health_check.model_dump() if payload.health_check else None,
        }
        # Nothing is committed until the Deploy is: a refused Deploy leaves no release behind.
        try:
            release = self.releases.add_release(
                actor=actor,
                membership=membership,
                payload=ReleaseCreate(
                    name=name,
                    version=self._next_version(organization_id, name, _tag(payload.image)),
                    kind=ReleaseKind.DOCKER,
                    description="Created by Run an app.",
                    spec=spec,
                ),
            )
            deployment = self.deployments.create_deployment(
                actor=actor,
                organization_id=organization_id,
                payload=DeploymentCreate(
                    release_id=release.id,
                    target=DeployTarget(device_ids=[device.id]),
                    name=f"Run {name} on {device.name}"[:255],
                    endpoint=self._endpoint(payload, release),
                ),
            )
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("app_run_conflict", "Someone else started this app just now. Try again.") from exc
        except Exception:
            self.session.rollback()
            raise
        return AppRunResponse(
            workload_key=name,
            release_id=release.id,
            deployment=deployment,
        )

    @staticmethod
    def _endpoint(payload: AppRunRequest, release: Release) -> EndpointSpec | None:
        if payload.endpoint is None:
            return None
        port = payload.endpoint.port
        if port is None:
            published = [b.port for b in docker_host_ports(release.spec) if b.protocol == "tcp"]
            if not published:
                raise ValidationAppError("endpoint_port_mismatch", "A public URL needs a published port.")
            port = published[0]
        return EndpointSpec(hostname=payload.endpoint.hostname, port=port)

    def _next_version(self, organization_id: uuid.UUID, name: str, tag: str) -> str:
        taken = set(
            self.session.scalars(
                select(Release.version).where(
                    Release.organization_id == organization_id,
                    Release.kind == ReleaseKind.DOCKER,
                    Release.name == name,
                )
            )
        )
        version, number = tag, 1
        while version in taken:
            number += 1
            version = f"{tag}-{number}"
        return version
