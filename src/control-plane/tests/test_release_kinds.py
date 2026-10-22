"""Unit tests for release kind validation."""

from __future__ import annotations

import pytest

from app.artifacts.models import ArtifactType
from app.core.exceptions import ValidationAppError
from app.releases.kinds import (
    ReleaseKind,
    validate_artifact_for_kind,
    validate_release_spec,
    workload_key_from_spec,
)


def test_systemd_spec_normalizes_unit_name() -> None:
    spec = validate_release_spec(ReleaseKind.SYSTEMD, {"unit_name": "  cam.service  "})
    assert spec == {"unit_name": "cam.service", "start": True}


def test_systemd_spec_requires_unit_name() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_release_spec(ReleaseKind.SYSTEMD, {})
    assert exc.value.code == "invalid_release_spec"


def test_docker_compose_spec() -> None:
    spec = validate_release_spec(
        ReleaseKind.DOCKER_COMPOSE,
        {"project_name": "stack", "env": {"FOO": "bar"}},
    )
    assert spec["project_name"] == "stack"
    assert spec["env"] == {"FOO": "bar"}


def test_artifact_mismatch() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_artifact_for_kind(
            kind=ReleaseKind.SYSTEMD,
            artifact_type=ArtifactType.OS_IMAGE,
            artifact_id="x",
            spec={"unit_name": "a.service"},
        )
    assert exc.value.code == "artifact_type_mismatch"


def test_workload_keys() -> None:
    assert workload_key_from_spec(ReleaseKind.SYSTEMD, {"unit_name": "a.service"}) == "a.service"
    assert workload_key_from_spec(ReleaseKind.DOCKER, {"container_name": "nginx"}) == "nginx"
    assert workload_key_from_spec(ReleaseKind.DOCKER_COMPOSE, {"project_name": "app"}) == "app"
