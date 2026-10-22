"""Release kind registry: which artifact types and specs each kind accepts."""

from __future__ import annotations

import enum
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


class DockerSpec(BaseModel):
    container_name: str = Field(min_length=1, max_length=255)
    image: str | None = Field(default=None, max_length=512)
    ports: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    restart: str | None = Field(default="unless-stopped", max_length=64)
    command: str | None = Field(default=None, max_length=1024)

    @field_validator("container_name")
    @classmethod
    def _strip_container_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("container_name must not be blank")
        return cleaned

    @field_validator("image")
    @classmethod
    def _strip_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class DockerComposeSpec(BaseModel):
    project_name: str = Field(min_length=1, max_length=255)
    env: dict[str, str] = Field(default_factory=dict)

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
        return model.model_validate(spec).model_dump()
    except Exception as exc:
        raise ValidationAppError(
            "invalid_release_spec",
            f"Invalid {kind.value} release spec: {exc}",
        ) from exc


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
