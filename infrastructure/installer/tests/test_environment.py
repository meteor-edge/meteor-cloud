"""Tests for Ansible extra-vars assembly used during deployment."""

from __future__ import annotations

from pathlib import Path

import pytest

from edge_installer.config.loader import load_configuration
from edge_installer.deployment.environment import build_ansible_extra_vars
from edge_installer.providers.aws.outputs import TerraformOutputs

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


def _outputs(**overrides: str) -> TerraformOutputs:
    defaults = {
        "instance_id": "i-123",
        "public_ip": "203.0.113.10",
        "elastic_ip": "",
        "private_ip": "10.0.0.5",
        "region": "eu-central-1",
        "ssh_username": "ubuntu",
        "security_group_id": "sg-abc",
    }
    defaults.update(overrides)
    return TerraformOutputs.model_validate(defaults)


def test_build_ansible_extra_vars_includes_observability_defaults() -> None:
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["observability_enabled"] == "false"
    assert extra["observability_backend"] == "prometheus"
    assert extra["log_format"] == "console"


def test_build_ansible_extra_vars_reflects_enabled_observability() -> None:
    config = load_configuration(EXAMPLE)
    config.observability.enabled = True
    config.observability.backend = "prometheus"

    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["observability_enabled"] == "true"
    assert extra["observability_backend"] == "prometheus"
    assert extra["log_format"] == "json"


def test_build_ansible_extra_vars_includes_core_deployment_fields() -> None:
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["installation_name"] == "production"
    assert extra["platform_version"] == "0.2.0"
    assert extra["platform_env"] == "production"
    assert extra["backend_image"] == config.deployment.backend_image
    assert extra["enabled_services"] == config.enabled_service_names()


def test_build_ansible_extra_vars_omits_vpn_key_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY", raising=False)
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())

    assert "vpn_server_private_key" not in extra


def test_build_ansible_extra_vars_includes_vpn_key_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY", "super-secret-key")
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["vpn_server_private_key"] == "super-secret-key"