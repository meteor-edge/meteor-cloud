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


def test_health_check_gets_defaults_and_is_left_out_when_unset() -> None:
    spec = validate_release_spec(
        ReleaseKind.DOCKER,
        {"container_name": "web", "image": "nginx", "health_check": {"http_url": "http://127.0.0.1/"}},
    )
    assert spec["health_check"] == {
        "timeout_seconds": 60,
        "stable_seconds": 10,
        "http_url": "http://127.0.0.1/",
        "rollback": True,
    }
    assert "health_check" not in validate_release_spec(ReleaseKind.SYSTEMD, {"unit_name": "cam.service"})


@pytest.mark.parametrize(
    "health_check",
    [{"timeout_seconds": 1}, {"timeout_seconds": 99999}, {"http_url": "file:///etc/passwd"}, {"stable_seconds": -1}],
)
def test_health_check_limits(health_check: dict) -> None:
    with pytest.raises(ValidationAppError):
        validate_release_spec(ReleaseKind.DOCKER_COMPOSE, {"project_name": "cam", "health_check": health_check})
