"""Installer configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from edge_installer.config.loader import load_configuration
from edge_installer.config.models import ObservabilitySettings
from edge_installer.config.validation import validate_configuration
from edge_installer.exceptions import ConfigurationError

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


def test_load_example_configuration() -> None:
    config = load_configuration(EXAMPLE)

    assert config.installation.name == "production"
    assert config.installation.provider == "aws"
    assert config.installation.environment == "production"
    assert config.platform.version == "0.2.0"
    assert config.aws.region == "eu-central-1"
    assert config.components.postgres.enabled is True
    assert config.observability.enabled is False
    assert config.observability.backend == "prometheus"


def test_missing_configuration_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(ConfigurationError, match="not found"):
        load_configuration(missing)


def test_invalid_configuration_reports_field(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "installation:\n"
        "  name: production\n"
        "  provider: azure\n"
        "  environment: production\n"
        "platform:\n"
        "  version: '0.2.0'\n"
        "aws:\n"
        "  region: eu-central-1\n"
        "  ssh_key_name: key\n"
        "  ssh_private_key_path: /tmp/key.pem\n"
        "network:\n"
        "  allowed_ssh_cidrs: ['203.0.113.10/32']\n"
        "deployment:\n"
        "  backend_image: edge-platform-backend:0.2.0\n"
        "  frontend_image: edge-platform-frontend:0.2.0\n"
        "services:\n"
        "  cloud_app:\n"
        "    enabled: true\n"
        "  vpn:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="installation.provider"):
        load_configuration(path)


def test_empty_configuration_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="empty"):
        load_configuration(path)


def test_validate_reports_missing_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.delenv("EDGE_PLATFORM_POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("EDGE_PLATFORM_JWT_SECRET", raising=False)

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    errors = validate_configuration(config)

    assert any("EDGE_PLATFORM_POSTGRES_PASSWORD" in item for item in errors)
    assert any("EDGE_PLATFORM_JWT_SECRET" in item for item in errors)


def test_validate_reports_missing_ssh_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(tmp_path / "missing.pem")
    errors = validate_configuration(config)

    assert any("ssh_private_key_path does not exist" in item for item in errors)


def test_validate_rejects_unimplemented_cloudwatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.observability.enabled = True
    config.observability.backend = "cloudwatch"
    errors = validate_configuration(config)

    assert any("cloudwatch is not implemented" in item for item in errors)

def test_validate_allows_disabled_cloudwatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cloudwatch is only rejected while observability is actually enabled."""
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.observability.enabled = False
    config.observability.backend = "cloudwatch"
    errors = validate_configuration(config)

    assert not any("cloudwatch" in item for item in errors)


def test_validate_allows_enabled_prometheus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.observability.enabled = True
    config.observability.backend = "prometheus"
    errors = validate_configuration(config)

    assert not any("cloudwatch" in item for item in errors)


def test_observability_settings_defaults() -> None:
    settings = ObservabilitySettings()

    assert settings.enabled is False
    assert settings.backend == "prometheus"


def test_observability_settings_rejects_unknown_backend() -> None:
    with pytest.raises(ValidationError):
        ObservabilitySettings(backend="datadog")


def test_observability_settings_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ObservabilitySettings(enabled=True, backend="prometheus", extra_field="nope")
