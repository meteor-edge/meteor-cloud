"""Release kind registry: which artifact types and specs each kind accepts."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.artifacts.models import ArtifactType
from app.core.exceptions import ValidationAppError


class ReleaseKind(enum.StrEnum):
    SYSTEMD = "systemd"
    DOCKER = "docker"
    DOCKER_COMPOSE = "docker_compose"


# Artifact type required for each kind. docker may omit an artifact when pulling by image.
REQUIRED_ARTIFACT_TYPE: dict[ReleaseKind, ArtifactType | None] = {
    ReleaseKind.SYSTEMD: ArtifactType.SYSTEMD,
    ReleaseKind.DOCKER: ArtifactType.DOCKER,
    ReleaseKind.DOCKER_COMPOSE: ArtifactType.DOCKER_COMPOSE,
}


class SystemdSpec(BaseModel):
    unit_name: str = Field(min_length=1, max_length=255)
    start: bool = True

    @field_validator("unit_name")
    @classmethod
    def _strip_unit_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("unit_name must not be blank")
        return cleaned


class MeteorCloudBinding(BaseModel):
    """Binds a workload to an application identity (contracts/data-plane-v1.md).

    The agent mints a device-scoped data-plane token for the application, mounts it
    into the workload as a file and sets the METEORCLOUD_* environment variables.
    """

    application: str = Field(min_length=2, max_length=63, pattern=r"^[a-z][a-z0-9-]{1,62}$")
    environment: str = Field(default="production", pattern=r"^[a-z][a-z0-9-]{0,31}$")


class DockerSpec(BaseModel):
    container_name: str = Field(min_length=1, max_length=255)
    image: str | None = Field(default=None, max_length=512)
    ports: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    restart: str | None = Field(default="unless-stopped", max_length=64)
    command: str | None = Field(default=None, max_length=1024)
    meteorcloud: MeteorCloudBinding | None = None

    @field_validator("container_name")
    @classmethod
    def _strip_container_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("container_name must not be blank")
        return cleaned

    @field_validator("ports")
    @classmethod
    def _check_ports(cls, value: list[str]) -> list[str]:
        return _reject_wide_host_ranges(value)

    @field_validator("image")
    @classmethod
    def _strip_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class DockerComposeSpec(BaseModel):
    project_name: str = Field(min_length=1, max_length=255)
    env: dict[str, str] = Field(default_factory=dict)
    meteorcloud: MeteorCloudBinding | None = None

    @field_validator("project_name")
    @classmethod
    def _strip_project_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("project_name must not be blank")
        return cleaned


_SPEC_MODELS: dict[ReleaseKind, type[BaseModel]] = {
    ReleaseKind.SYSTEMD: SystemdSpec,
    ReleaseKind.DOCKER: DockerSpec,
    ReleaseKind.DOCKER_COMPOSE: DockerComposeSpec,
}


def validate_release_spec(kind: ReleaseKind, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize kind-specific install config.

    Raise ValidationAppError when the spec does not match the kind.
    """
    model = _SPEC_MODELS[kind]
    try:
        validated = model.model_validate(spec).model_dump()
    except Exception as exc:
        raise ValidationAppError(
            "invalid_release_spec",
            f"Invalid {kind.value} release spec: {exc}",
        ) from exc
    # Unbound workloads keep the spec shape they had before application bindings existed.
    if validated.get("meteorcloud") is None:
        validated.pop("meteorcloud", None)
    return validated


def validate_artifact_for_kind(
    *,
    kind: ReleaseKind,
    artifact_type: ArtifactType | None,
    artifact_id: Any,
    spec: dict[str, Any],
) -> None:
    """Enforce artifact presence and type rules for a release kind.

    Raise ValidationAppError when the combination is invalid.
    """
    required = REQUIRED_ARTIFACT_TYPE[kind]
    if kind == ReleaseKind.DOCKER:
        has_image = bool(spec.get("image"))
        has_artifact = artifact_id is not None
        if not has_image and not has_artifact:
            raise ValidationAppError(
                "invalid_release_spec",
                "A docker release needs either an image name or a docker artifact tarball.",
            )
        if has_artifact and artifact_type is not None and artifact_type != ArtifactType.DOCKER:
            raise ValidationAppError(
                "artifact_type_mismatch",
                f"A docker release requires a docker artifact, not {artifact_type.value}.",
            )
        return

    if artifact_id is None or artifact_type is None:
        raise ValidationAppError(
            "artifact_required",
            f"A {kind.value} release requires an artifact.",
        )
    if required is not None and artifact_type != required:
        raise ValidationAppError(
            "artifact_type_mismatch",
            f"A {kind.value} release requires a {required.value} artifact, not {artifact_type.value}.",
        )


def workload_key_from_spec(kind: ReleaseKind, spec: dict[str, Any]) -> str:
    """Stable per-device workload key derived from the install spec."""
    if kind == ReleaseKind.SYSTEMD:
        return str(spec["unit_name"])
    if kind == ReleaseKind.DOCKER:
        return str(spec["container_name"])
    return str(spec["project_name"])


@dataclass(frozen=True)
class HostPortBinding:
    """A port a docker release binds on the device itself (the left side of ``host:container``)."""

    port: int
    host_ip: str | None
    protocol: str

    @property
    def loopback_only(self) -> bool:
        """Bound to localhost: other workloads are unaffected, but WireGuard cannot reach it."""
        return self.host_ip in {"127.0.0.1", "::1", "localhost"}


_MAX_RANGE = 1024


def _port_range(text: str) -> list[int]:
    start, _, end = text.partition("-")
    first = int(start)
    last = int(end) if end else first
    if not (1 <= first <= last <= 65535) or last - first >= _MAX_RANGE:
        return []
    return list(range(first, last + 1))


def _split_port_entry(raw: Any) -> tuple[str | None, str, str] | None:
    """``(host_ip, host_ports_text, protocol)`` for a ``host:container`` entry, else None."""
    text = str(raw).strip()
    mapping, _, protocol = text.partition("/")
    protocol = (protocol or "tcp").lower()
    host_ip: str | None = None
    if mapping.startswith("["):
        closing = mapping.find("]")
        if closing == -1:
            return None
        host_ip = mapping[1:closing]
        mapping = mapping[closing + 1 :].lstrip(":")
        parts = mapping.split(":")
    else:
        parts = mapping.split(":")
        if len(parts) == 3:
            host_ip = parts.pop(0) or None
    if len(parts) != 2 or not parts[0]:
        return None
    return host_ip, parts[0], protocol


def docker_host_ports(spec: dict[str, Any]) -> list[HostPortBinding]:
    """Host ports a docker release spec publishes, from Docker's ``ports`` short syntax.

    ``"8080:80"``, ``"127.0.0.1:8080:80"``, ``"[::1]:8080:80"``, ``"8000-8001:8000-8001"`` and
    ``"8080:80/udp"`` bind host ports; ``"80"`` (container only) gets a random host port and
    cannot conflict, so it is left out. Unparseable entries are skipped. New releases cannot
    declare a host range of ``_MAX_RANGE`` ports or more (see ``DockerSpec``).
    """
    bindings: list[HostPortBinding] = []
    for raw in spec.get("ports") or []:
        entry = _split_port_entry(raw)
        if entry is None:
            continue
        host_ip, host_ports, protocol = entry
        try:
            ports = _port_range(host_ports)
        except ValueError:
            continue
        if host_ip in {"0.0.0.0", "::"}:
            host_ip = None
        bindings.extend(HostPortBinding(port=port, host_ip=host_ip, protocol=protocol) for port in ports)
    return bindings


def _reject_wide_host_ranges(ports: list[str]) -> list[str]:
    """Port conflict checks expand host ranges, so a range that would be dropped is refused."""
    for raw in ports:
        entry = _split_port_entry(raw)
        if entry is None:
            continue
        start, _, end = entry[1].partition("-")
        try:
            first, last = int(start), int(end) if end else int(start)
        except ValueError:
            continue
        if last - first >= _MAX_RANGE:
            raise ValueError(
                f"port range {entry[1]} spans {last - first + 1} host ports; "
                f"publish fewer than {_MAX_RANGE} ports per entry"
            )
    return ports
