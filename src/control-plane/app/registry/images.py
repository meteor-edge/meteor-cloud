"""Images of the organization's own registry in releases: names, digests, platforms, device pulls."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, NotFoundError, ValidationAppError
from app.deployments.software_models import DeviceSoftware, SoftwareSyncStatus
from app.devices.models import Device
from app.ports.registry import ContainerRegistry, RegistryUnavailableError
from app.registry.access import sign_pull_password, verify_pull_password
from app.registry.catalog import registry_client
from app.registry.models import ContainerImage, RegistryNamespace
from app.registry.schemas import PULL_PASSWORD_SECONDS, REFERENCE_RE, AgentPullTokenResponse
from app.registry.service import namespace_for
from app.tenancy.models import Organization

REGISTRY_NAME = "meteorcloud"
PULL_USERNAME = "device"

# What devices report (uname -m, or Go's names) -> the architecture in an image.
_ARCHITECTURES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv8l": "arm",
    "armv7l": "arm",
    "armv7": "arm",
    "armhf": "arm",
    "armv6l": "arm",
    "armv6": "arm",
    "arm": "arm",
    "i386": "386",
    "i686": "386",
    "x86": "386",
    "riscv64": "riscv64",
}
# 64-bit CPUs also run these.
_ALSO_RUNS = {"amd64": {"386"}, "arm64": {"arm"}}


def image_architecture(device_architecture: str | None) -> str | None:
    return _ARCHITECTURES.get((device_architecture or "").strip().lower())


class RegistryImages:
    def __init__(
        self, session: Session, settings: Settings | None = None, registry: ContainerRegistry | None = None
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.registry = registry or registry_client(session, self.settings)

    # --- releases -----------------------------------------------------------

    def pin(self, organization_id: uuid.UUID, spec: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        """Full image name plus the digest devices pull. ``image`` is ``web:1.0`` or the full name."""
        if self.registry is None:
            raise ValidationAppError("registry_disabled", "The container registry is not enabled on this platform.")
        name = str(spec.get("image") or "").strip()
        namespace = namespace_for(self.session, self.session.get(Organization, organization_id))
        prefix = f"{self.settings.registry_public_host}/{namespace}/"
        match = REFERENCE_RE.fullmatch(name.removeprefix(prefix))
        if match is None or name.startswith(f"{self.settings.registry_public_host}/") != name.startswith(prefix):
            raise ValidationAppError(
                "invalid_release_spec",
                f"{name!r} is not an image of this organization. Use a name such as web:1.0.",
            )
        repository, tag, digest = match["repository"], match["tag"] or "latest", match["digest"]
        image = f"{prefix}{repository}:{tag}"
        pinned = {**spec, "image": image}
        if previous and previous.get("image") == image and previous.get("registry") == REGISTRY_NAME:
            if not digest and spec.get("image_digest") in (None, previous.get("image_digest")):
                return {**pinned, "image_digest": previous["image_digest"]}
        wanted = digest or spec.get("image_digest") or tag
        try:
            resolved = self.registry.digest(f"{namespace}/{repository}", wanted)
        except RegistryUnavailableError as exc:
            raise AppError("registry_unavailable", str(exc), status_code=503) from exc
        if resolved is None:
            raise ValidationAppError("image_not_found", f"{repository}:{tag} is not in this organization's registry.")
        return {**pinned, "image_digest": resolved}

    def platform_mismatch(self, organization_id: uuid.UUID, spec: dict[str, Any], device: Device) -> str | None:
        """Why the image cannot run on the device, or None when it can (or nobody knows)."""
        if spec.get("registry") != REGISTRY_NAME:
            return None
        architecture = image_architecture(device.architecture)
        if architecture is None:
            return None
        platforms = self.session.scalar(
            select(ContainerImage.platforms)
            .where(
                ContainerImage.organization_id == organization_id,
                ContainerImage.digest == spec.get("image_digest"),
            )
            .limit(1)
        )
        built_for = {p["architecture"] for p in platforms or [] if p["os"] == "linux"}
        if not platforms or built_for & ({architecture} | _ALSO_RUNS.get(architecture, set())):
            return None
        names = ", ".join(sorted(f"{p['os']}/{p['architecture']}" for p in platforms))
        return f"The image is built for {names}, but the device is linux/{architecture}."

    # --- devices ------------------------------------------------------------

    def _desired_repository(self, device: Device, workload_key: str) -> str | None:
        """namespace/repository of the workload's desired image, if it is in our registry."""
        row = self.session.scalar(
            select(DeviceSoftware).where(
                DeviceSoftware.device_id == device.id,
                DeviceSoftware.workload_key == workload_key,
                DeviceSoftware.organization_id == device.organization_id,
            )
        )
        if row is None or row.desired_release_id is None or row.sync_status == SoftwareSyncStatus.CANCELLED.value:
            return None
        spec = row.desired_spec or {}
        host = f"{self.settings.registry_public_host}/"
        image = str(spec.get("image") or "")
        if spec.get("registry") != REGISTRY_NAME or not spec.get("image_digest") or not image.startswith(host):
            return None
        repository = image.removeprefix(host).rpartition(":")[0]
        namespace = self.session.get(RegistryNamespace, repository.partition("/")[0])
        # Specs are written by us, but the namespace decides whose images a device can read.
        if namespace is None or namespace.organization_id != device.organization_id:
            return None
        return repository

    def issue_pull_token(self, *, device: Device, workload_key: str) -> AgentPullTokenResponse:
        """A short-lived password that can only pull the repository an operator deployed to this device."""
        repository = self._desired_repository(device, workload_key) if self.registry is not None else None
        if repository is None:
            raise NotFoundError("registry_image_not_desired", "This workload has no image in the registry.")
        password, expires_at = sign_pull_password(
            self.session, self.settings, device_id=device.id, workload_key=workload_key, ttl=PULL_PASSWORD_SECONDS
        )
        self.session.commit()
        return AgentPullTokenResponse(
            registry=self.settings.registry_public_host,
            username=PULL_USERNAME,
            password=password,
            expires_at=expires_at,
        )

    def pullable_repository(self, password: str) -> tuple[Device, str] | None:
        """The device behind a pull password and what it may pull now; None when it is not valid (any more)."""
        try:
            claims = verify_pull_password(self.session, self.settings, password)
            device = self.session.get(Device, uuid.UUID(claims["sub"]))
        except (jwt.PyJWTError, KeyError, ValueError):
            return None
        if device is None or not device.is_enabled or device.credential_hash is None:
            return None
        # Checked again: the release may be gone since the password was issued.
        repository = self._desired_repository(device, str(claims.get("workload_key")))
        return (device, repository) if repository is not None else None
