"""Images of the organization's own registry in releases: names, digests, platforms, device pulls."""

from __future__ import annotations

import logging
import re
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

logger = logging.getLogger(__name__)

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
# The newest 32-bit ARM version the CPU runs, where its name says so. It runs the older ones too.
_ARM_VERSIONS = {"armv6": 6, "armv6l": 6, "armv7": 7, "armv7l": 7}


def image_architecture(device_architecture: str | None) -> str | None:
    return _ARCHITECTURES.get((device_architecture or "").strip().lower())


def _runs(platform: dict[str, Any], device_architecture: str | None) -> bool:
    architecture = image_architecture(device_architecture) or ""
    if platform["architecture"] not in {architecture} | _ALSO_RUNS.get(architecture, set()):
        return False
    newest = _ARM_VERSIONS.get((device_architecture or "").strip().lower())
    variant = re.fullmatch(r"v([0-9])", platform.get("variant") or "")
    return newest is None or variant is None or int(variant[1]) <= newest


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

    def platforms(self, organization_id: uuid.UUID, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """What the release's pinned image is built for; empty when it is not in our registry or nobody knows."""
        if spec.get("registry") != REGISTRY_NAME:
            return []
        platforms = self.session.scalar(
            select(ContainerImage.platforms)
            .where(
                ContainerImage.organization_id == organization_id,
                ContainerImage.digest == spec.get("image_digest"),
            )
            .limit(1)
        )
        if platforms is not None:
            return platforms
        # Its tag moved or was deleted since the release: the catalog only lists current tags.
        repository = str(spec.get("image") or "").removeprefix(f"{self.settings.registry_public_host}/")
        owner = self.session.get(RegistryNamespace, repository.partition("/")[0])
        if self.registry is None or owner is None or owner.organization_id != organization_id:
            return []
        try:
            manifest = self.registry.manifest(repository.rpartition(":")[0], str(spec.get("image_digest")))
        except RegistryUnavailableError as exc:
            logger.warning("Could not read the platforms of %s: %s", repository, exc)
            return []
        if manifest is None:
            return []
        return [{"os": p.os, "architecture": p.architecture, "variant": p.variant} for p in manifest.platforms]

    @staticmethod
    def platform_mismatch(platforms: list[dict[str, Any]], device: Device) -> str | None:
        """Why an image for these platforms cannot run on the device, or None when it can (or nobody knows)."""
        architecture = image_architecture(device.architecture)
        if architecture is None or not platforms:
            return None
        if any(p["os"] == "linux" and _runs(p, device.architecture) for p in platforms):
            return None
        names = ", ".join(
            sorted("/".join(filter(None, (p["os"], p["architecture"], p.get("variant")))) for p in platforms)
        )
        version = _ARM_VERSIONS.get((device.architecture or "").strip().lower())
        variant = f"/v{version}" if version else ""
        return f"The image is built for {names}, but the device is linux/{architecture}{variant}."

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
