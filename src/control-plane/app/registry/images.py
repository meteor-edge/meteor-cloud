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
from app.registry.catalog import pin_tag, registry_client
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


def registry_images(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Full name and digest of the images of a release that are in our registry."""
    if spec.get("registry_images"):  # Compose
        return [(image, digest) for image, _, digest in (str(e).partition("@") for e in spec["registry_images"])]
    if spec.get("registry") == REGISTRY_NAME and spec.get("image_digest"):
        return [(str(spec.get("image")), str(spec["image_digest"]))]
    return []


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
        kept = None
        if previous and previous.get("registry") == REGISTRY_NAME:
            kept = {str(previous.get("image")): str(previous.get("image_digest"))}
        image, digest = self._resolve(organization_id, str(spec.get("image") or ""), spec.get("image_digest"), kept)
        return {**spec, "image": image, "image_digest": digest}

    def pin_all(
        self, organization_id: uuid.UUID, spec: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        """The same for a Compose release's ``registry_images``: ``full/name:tag@sha256:…``."""
        kept = dict(entry.split("@", 1) for entry in (previous or {}).get("registry_images") or [])
        pinned: dict[str, str] = {}
        for entry in spec["registry_images"]:
            image, digest = self._resolve(organization_id, entry, None, kept)
            if pinned.setdefault(image, digest) != digest:
                raise ValidationAppError("invalid_release_spec", f"{image} is listed with two digests.")
        return {**spec, "registry_images": [f"{image}@{digest}" for image, digest in sorted(pinned.items())]}

    def _resolve(
        self, organization_id: uuid.UUID, name: str, given_digest: str | None, kept: dict[str, str] | None
    ) -> tuple[str, str]:
        if self.registry is None:
            raise ValidationAppError("registry_disabled", "The container registry is not enabled on this platform.")
        name = name.strip()
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
        if kept and image in kept and not digest and given_digest in (None, kept[image]):
            return image, kept[image]
        wanted = digest or given_digest or tag
        try:
            resolved = self.registry.digest(f"{namespace}/{repository}", wanted)
        except RegistryUnavailableError as exc:
            raise AppError("registry_unavailable", str(exc), status_code=503) from exc
        if resolved is None:
            raise ValidationAppError("image_not_found", f"{repository}:{tag} is not in this organization's registry.")
        self._keep(f"{namespace}/{repository}", resolved)
        return image, resolved

    def _keep(self, repository: str, digest: str) -> None:
        """A hidden tag, so garbage collection keeps the image when its own tag moves or is deleted."""
        try:
            self.registry.tag(repository, digest, pin_tag(digest))
        except RegistryUnavailableError as exc:
            raise AppError("registry_unavailable", str(exc), status_code=503) from exc

    def platform_mismatch(self, organization_id: uuid.UUID, spec: dict[str, Any], device: Device) -> str | None:
        """Why the image cannot run on the device, or None when it can (or nobody knows)."""
        architecture = image_architecture(device.architecture)
        if architecture is None:
            return None
        for image, digest in registry_images(spec):
            platforms = self.session.scalar(
                select(ContainerImage.platforms)
                .where(ContainerImage.organization_id == organization_id, ContainerImage.digest == digest)
                .limit(1)
            )
            built_for = {p["architecture"] for p in platforms or [] if p["os"] == "linux"}
            if not platforms or built_for & ({architecture} | _ALSO_RUNS.get(architecture, set())):
                continue
            names = ", ".join(sorted(f"{p['os']}/{p['architecture']}" for p in platforms))
            return f"The image {image} is built for {names}, but the device is linux/{architecture}."
        return None

    # --- devices ------------------------------------------------------------

    def _desired_repositories(self, device: Device, workload_key: str) -> set[str]:
        """namespace/repository of the workload's desired images that are in our registry."""
        row = self.session.scalar(
            select(DeviceSoftware).where(
                DeviceSoftware.device_id == device.id,
                DeviceSoftware.workload_key == workload_key,
                DeviceSoftware.organization_id == device.organization_id,
            )
        )
        if row is None or row.desired_release_id is None or row.sync_status == SoftwareSyncStatus.CANCELLED.value:
            return set()
        host = f"{self.settings.registry_public_host}/"
        found = set()
        for image, _ in registry_images(row.desired_spec or {}):
            repository = image.removeprefix(host).rpartition(":")[0]
            namespace = self.session.get(RegistryNamespace, repository.partition("/")[0])
            # Specs are written by us, but the namespace decides whose images a device can read.
            if image.startswith(host) and namespace and namespace.organization_id == device.organization_id:
                found.add(repository)
        return found

    def issue_pull_token(self, *, device: Device, workload_key: str) -> AgentPullTokenResponse:
        """A short-lived password that can only pull the repositories an operator deployed to this device."""
        if self.registry is None or not self._desired_repositories(device, workload_key):
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

    def pullable_repositories(self, password: str) -> tuple[Device, set[str]] | None:
        """The device behind a pull password and what it may pull now; None when it is not valid (any more)."""
        try:
            claims = verify_pull_password(self.session, self.settings, password)
            device = self.session.get(Device, uuid.UUID(claims["sub"]))
        except (jwt.PyJWTError, KeyError, ValueError):
            return None
        if device is None or not device.is_enabled or device.credential_hash is None:
            return None
        # Checked again: the release may be gone since the password was issued.
        repositories = self._desired_repositories(device, str(claims.get("workload_key")))
        return (device, repositories) if repositories else None
