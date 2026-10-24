"""EC2 size follows the containers aws-up will start."""

from __future__ import annotations

from pathlib import Path

from edge_installer.config.loader import load_configuration
from edge_installer.deployment.sizing import recommend_host_size, resolve_aws_host

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


def test_git_build_needs_medium() -> None:
    config = load_configuration(EXAMPLE)
    size = recommend_host_size(config)
    assert size.instance_type == "t3.medium"
    assert "git_build" in size.reasons
    assert size.root_volume_size_gb >= 40


def test_registry_without_extras_fits_small() -> None:
    config = load_configuration(EXAMPLE)
    config.deployment.image_source = "registry"
    size = recommend_host_size(config)
    assert size.instance_type == "t3.small"
    assert "git_build" not in size.reasons


def test_observability_needs_large() -> None:
    config = load_configuration(EXAMPLE)
    config.observability.enabled = True
    size = recommend_host_size(config)
    assert size.instance_type == "t3.large"


def test_arm64_uses_t4g() -> None:
    config = load_configuration(EXAMPLE)
    assert config.aws is not None
    config.aws.architecture = "arm64"
    size = recommend_host_size(config)
    assert size.instance_type.startswith("t4g.")


def test_auto_uses_recommendation() -> None:
    config = load_configuration(EXAMPLE)
    assert config.aws is not None
    config.aws.instance_type = "auto"
    instance_type, disk, size = resolve_aws_host(config)
    assert instance_type == size.instance_type
    assert disk == size.root_volume_size_gb


def test_undersized_explicit_type_is_raised() -> None:
    config = load_configuration(EXAMPLE)
    assert config.aws is not None
    config.aws.instance_type = "t3.small"
    instance_type, _, size = resolve_aws_host(config)
    assert size.instance_type == "t3.medium"
    assert instance_type == "t3.medium"


def test_larger_explicit_type_is_kept() -> None:
    config = load_configuration(EXAMPLE)
    assert config.aws is not None
    config.aws.instance_type = "t3.xlarge"
    instance_type, _, _ = resolve_aws_host(config)
    assert instance_type == "t3.xlarge"


def test_unknown_type_is_kept() -> None:
    config = load_configuration(EXAMPLE)
    assert config.aws is not None
    config.aws.instance_type = "m5.large"
    instance_type, _, _ = resolve_aws_host(config)
    assert instance_type == "m5.large"


def test_explicit_larger_disk_is_kept() -> None:
    config = load_configuration(EXAMPLE)
    assert config.aws is not None
    config.aws.root_volume_size_gb = 80
    _, disk, _ = resolve_aws_host(config)
    assert disk == 80
