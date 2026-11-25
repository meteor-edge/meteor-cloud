"""Run an app: a release made from an image plus a Deploy to one device, in one request."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
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
from app.deployments.software_models import DeviceSoftware, SoftwareSyncStatus
from app.devices.models import Device
from app.identity.models import User
from app.registry.models import ContainerImage
from app.releases.kinds import DockerResources, HealthCheck, ReleaseKind, docker_host_ports
from app.releases.models import Release
from app.releases.schemas import ReleaseCreate
from app.releases.secrets import ReleaseSecrets
from app.releases.service import ReleaseService
from app.tenancy.models import OrganizationMembership

_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9_.-]{0,61}[a-z0-9])?")
_VERSION_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _one_line(values: dict[str, Any]) -> dict[str, Any]:
    """Devices pass the environment as lines."""
    for name, value in values.items():
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if text is not None and any(char in text for char in "\n\r\0"):
            raise ValueError(f"the value of {name} contains a line break")
    return values


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
    volumes: list[str] = Field(default_factory=list, max_length=20)
    resources: DockerResources | None = None
    # Stored encrypted; only the names are in the release.
    secret_env: dict[str, SecretStr] = Field(default_factory=dict, max_length=100)
    endpoint: AppEndpoint | None = None

    _lines = field_validator("env", "secret_env")(_one_line)

    @model_validator(mode="after")
    def _no_name_twice(self) -> AppRunRequest:
        both = set(self.env) & set(self.secret_env)
        if both:
            raise ValueError(f"{', '.join(sorted(both))} is in env and in secret_env")
        return self

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower()
        if not value:
            return None
        if not _NAME_RE.fullmatch(value):
            raise ValueError("use lowercase letters, digits, '.', '-' and '_'")
        return value


class AppRedeployRequest(BaseModel):
    """What to change; everything left out stays as it is."""

    image: str | None = Field(default=None, min_length=1, max_length=512)
    ports: list[str] | None = Field(default=None, max_length=20)
    env: dict[str, str] | None = Field(default=None, max_length=100)
    command: str | None = Field(default=None, max_length=1024)
    volumes: list[str] | None = Field(default=None, max_length=20)
    resources: DockerResources | None = None
    # Values to set; null removes one. Others keep their value.
    secret_env: dict[str, SecretStr | None] = Field(default_factory=dict, max_length=100)

    _lines = field_validator("secret_env")(_one_line)

    @field_validator("env")
    @classmethod
    def _env_lines(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return value and _one_line(value)


class AppResponse(BaseModel):
    workload_key: str
    release_id: uuid.UUID
    release_version: str
    # Names, not values, for secret_env.
    spec: dict[str, Any]
    health: str
    sync_status: str
    # For images of the organization's registry: short name and tag.
    repository: str | None = None
    tag: str | None = None
    # The tag points to another image than the one deployed.
    tag_moved: bool = False
    # A tag of the repository pushed after the deployed one.
    newer_tag: str | None = None


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


def _active(row: DeviceSoftware) -> bool:
    return row.desired_release_id is not None and row.sync_status != SoftwareSyncStatus.CANCELLED.value


class AppService:
    def __init__(self, deployments: DeploymentService, releases: ReleaseService) -> None:
        self.session = deployments.session
        self.deployments = deployments
        self.releases = releases

    def run(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, payload: AppRunRequest
    ) -> AppRunResponse:
        membership, device = self._authorize(actor, organization_id, device_id, "deployment.create")
        if payload.endpoint is not None:
            self.deployments.authz.require(membership, "endpoint.create")

        name = payload.name or _default_name(payload.image)
        existing = self.deployments.software_rows.get_by_workload(device_id=device.id, workload_key=name)
        if existing is not None and _active(existing):
            raise ConflictError("app_exists", f"This device already runs an app named {name}. Choose another name.")

        spec = {
            "container_name": name,
            "image": payload.image.strip(),
            "ports": payload.ports,
            "env": payload.env,
            "command": payload.command,
            "registry": payload.registry,
            "volumes": payload.volumes,
            "resources": payload.resources.model_dump() if payload.resources else None,
            "secret_env": sorted(payload.secret_env),
            "health_check": payload.health_check.model_dump() if payload.health_check else None,
        }
        return self._deploy(
            actor=actor,
            membership=membership,
            device=device,
            spec=spec,
            secrets={key: value.get_secret_value() for key, value in payload.secret_env.items()},
            endpoint=payload.endpoint,
            title="Run",
        )

    def redeploy(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        workload_key: str,
        payload: AppRedeployRequest,
    ) -> AppRunResponse:
        """A new release from the app's current one with some changes, deployed to this device."""
        membership, device = self._authorize(actor, organization_id, device_id, "deployment.create")
        current = self._current_release(device, workload_key)
        changes = payload.model_dump(exclude_unset=True, exclude={"secret_env"})
        if changes.get("image") is None:
            changes.pop("image", None)
        spec = {**current.spec, **{key: value for key, value in changes.items()}}
        # A public image is pinned again, and "web:latest" in our registry means what it is now.
        spec.pop("image_digest", None)
        for key in ("ports", "env", "volumes"):
            spec[key] = spec.get(key) or ([] if key != "env" else {})
        secrets = ReleaseSecrets(self.session, self.releases.settings).values(current.id)
        for key, value in payload.secret_env.items():
            if value is None:
                secrets.pop(key, None)
            else:
                secrets[key] = value.get_secret_value()
                spec["env"] = {k: v for k, v in spec["env"].items() if k != key}
        clash = set(secrets) & set(spec["env"])
        if clash:
            raise ValidationAppError("invalid_release_spec", f"{', '.join(sorted(clash))} is a secret variable.")
        spec["secret_env"] = sorted(secrets)
        return self._deploy(
            actor=actor,
            membership=membership,
            device=device,
            spec=spec,
            secrets=secrets,
            endpoint=None,
            title="Redeploy",
        )

    def list_apps(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> list[AppResponse]:
        _, device = self._authorize(actor, organization_id, device_id, "device.read")
        apps = []
        for row in self.deployments.software_rows.list_for_device(device_id=device.id):
            if row.kind != ReleaseKind.DOCKER.value or not _active(row):
                continue
            release = self.session.get(Release, row.desired_release_id)
            if release is None:
                continue
            app = AppResponse(
                workload_key=row.workload_key,
                release_id=release.id,
                release_version=release.version,
                spec=release.spec,
                health=row.health,
                sync_status=row.sync_status,
            )
            self._add_updates(organization_id, app)
            apps.append(app)
        return apps

    def _add_updates(self, organization_id: uuid.UUID, app: AppResponse) -> None:
        if app.spec.get("registry") != "meteorcloud":
            return
        full, _, app.tag = str(app.spec.get("image")).rpartition(":")
        app.repository = full.split("/", 2)[-1]
        images = self.session.scalars(
            select(ContainerImage)
            .where(ContainerImage.organization_id == organization_id, ContainerImage.repository == app.repository)
            .order_by(ContainerImage.pushed_at.desc())
        ).all()
        current = next((image for image in images if image.tag == app.tag), None)
        digest = app.spec.get("image_digest")
        app.tag_moved = current is not None and current.digest != digest
        if images and images[0].tag != app.tag and images[0].digest != digest:
            if current is None or images[0].pushed_at > current.pushed_at:
                app.newer_tag = images[0].tag

    def _authorize(
        self, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, permission: str
    ) -> tuple[OrganizationMembership, Device]:
        authz = self.deployments.authz
        membership = authz.require_membership(user_id=actor.id, organization_id=organization_id)
        device = self.deployments.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        authz.require(membership, permission, device_group_id=device.device_group_id)
        return membership, device

    def _current_release(self, device: Device, workload_key: str) -> Release:
        row = self.deployments.software_rows.get_by_workload(device_id=device.id, workload_key=workload_key)
        release = self.session.get(Release, row.desired_release_id) if row is not None and _active(row) else None
        if release is None or release.kind != ReleaseKind.DOCKER or not release.spec.get("image"):
            raise NotFoundError("app_not_found", "This device runs no such app.")
        if release.artifact_id is not None:
            raise ValidationAppError("app_not_editable", "An app installed from an artifact is changed with a release.")
        return release

    def _deploy(
        self,
        *,
        actor: User,
        membership: OrganizationMembership,
        device: Device,
        spec: dict[str, Any],
        secrets: dict[str, str],
        endpoint: AppEndpoint | None,
        title: str,
    ) -> AppRunResponse:
        organization_id, name = membership.organization_id, spec["container_name"]
        # Nothing is committed until the Deploy is: a refused Deploy leaves no release behind.
        try:
            release = self.releases.add_release(
                actor=actor,
                membership=membership,
                payload=ReleaseCreate(
                    name=name,
                    version=self._next_version(organization_id, name, _tag(spec["image"])),
                    kind=ReleaseKind.DOCKER,
                    description=f"{title} an app.",
                    spec=spec,
                ),
                secrets=secrets,
            )
            deployment = self.deployments.create_deployment(
                actor=actor,
                organization_id=organization_id,
                payload=DeploymentCreate(
                    release_id=release.id,
                    target=DeployTarget(device_ids=[device.id]),
                    name=f"{title} {name} on {device.name}"[:255],
                    endpoint=self._endpoint(endpoint, release),
                ),
            )
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("app_run_conflict", "Someone else started this app just now. Try again.") from exc
        except Exception:
            self.session.rollback()
            raise
        return AppRunResponse(workload_key=name, release_id=release.id, deployment=deployment)

    @staticmethod
    def _endpoint(endpoint: AppEndpoint | None, release: Release) -> EndpointSpec | None:
        if endpoint is None:
            return None
        port = endpoint.port
        if port is None:
            published = [b.port for b in docker_host_ports(release.spec) if b.protocol == "tcp"]
            if not published:
                raise ValidationAppError("endpoint_port_mismatch", "A public URL needs a published port.")
            port = published[0]
        return EndpointSpec(hostname=endpoint.hostname, port=port)

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
