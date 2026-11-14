"""Installer configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from edge_installer.config.loader import load_configuration
from edge_installer.config.models import InstallationSettings, ObservabilitySettings
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


def test_validate_allows_external_postgres_with_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.delenv("EDGE_PLATFORM_POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")
    monkeypatch.setenv(
        "EDGE_PLATFORM_DATABASE_URL",
        "postgresql+psycopg://edge:secret@db.example.com:5432/edge_platform",
    )

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.components.postgres.enabled = False
    errors = validate_configuration(config)

    assert not any("POSTGRES_PASSWORD" in item for item in errors)
    assert not any("components.postgres must be enabled" in item for item in errors)
    assert not any("EDGE_PLATFORM_DATABASE_URL" in item for item in errors)


def test_validate_rejects_disabled_postgres_without_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.delenv("EDGE_PLATFORM_POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("EDGE_PLATFORM_DATABASE_URL", raising=False)
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.components.postgres.enabled = False
    errors = validate_configuration(config)

    assert any("EDGE_PLATFORM_DATABASE_URL" in item for item in errors)


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


def test_validate_requires_connectivity_token_for_vpn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.delenv("EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("EDGE_PLATFORM_MQTT_INTERNAL_TOKEN", raising=False)

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    assert any("EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN" in item for item in validate_configuration(config))

    monkeypatch.setenv("EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN", "token")
    assert not any("CONNECTIVITY" in item for item in validate_configuration(config))


def test_validate_requires_url_safe_mqtt_token(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_configuration(EXAMPLE)
    monkeypatch.setenv("EDGE_PLATFORM_MQTT_INTERNAL_TOKEN", "abc/def+==")
    assert any("MQTT_INTERNAL_TOKEN" in item for item in validate_configuration(config))
    monkeypatch.setenv("EDGE_PLATFORM_MQTT_INTERNAL_TOKEN", "Abc_def-123.x~")
    assert not any("MQTT_INTERNAL_TOKEN" in item for item in validate_configuration(config))


def test_vpn_placement_defaults_to_same_host() -> None:
    config = load_configuration(EXAMPLE)
    assert config.services.vpn.placement == "same_host"


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



def test_gcp_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InstallationSettings(name="demo", environment="production", provider="gcp")


def test_validate_dns_zone_needs_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")

    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.aws.dns_zone_id = "Z123"
    config.platform.domain = None
    errors = validate_configuration(config)

    assert any("dns_zone_id requires platform.domain" in item for item in errors)

    config.platform.domain = "fleet.example.com"
    assert not any("dns_zone_id" in item for item in validate_configuration(config))


def test_external_mqtt_requires_broker_host() -> None:
    from edge_installer.config.models import MqttServiceSettings

    with pytest.raises(ValueError, match="broker_host"):
        MqttServiceSettings(enabled=True, broker="external")
    with pytest.raises(ValueError, match="8883"):
        MqttServiceSettings(enabled=True, broker="bundled", broker_port=1883)


def test_observability_components_and_retention_defaults() -> None:
    settings = ObservabilitySettings()

    assert settings.components.enabled_names() == ["metrics", "logs", "alerting", "dashboards"]
    assert settings.log_retention_hours == 168
    assert settings.metrics_retention_days == 15
    assert settings.metrics_retention_size == "5GB"


@pytest.mark.parametrize(
    "overrides",
    [
        {"log_retention_hours": 1},
        {"metrics_retention_days": 0},
        {"metrics_retention_size": "5TB"},
        {"metrics_retention_size": "lots"},
        {"components": {"tracing": True}},
    ],
)
def test_observability_settings_reject_bad_values(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        ObservabilitySettings(**overrides)


def _enabled_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key_path = tmp_path / "key.pem"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "secret")
    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(key_path)
    config.observability.enabled = True
    monkeypatch.setenv("EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD", "grafana-secret")
    return config


def test_validate_observability_component_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path, monkeypatch)
    config.observability.components.metrics = False
    config.observability.components.logs = False

    errors = validate_configuration(config)

    assert any("alerting needs components.metrics" in item for item in errors)
    assert any("dashboards needs components.metrics or components.logs" in item for item in errors)


def test_validate_allows_logs_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path, monkeypatch)
    config.observability.components.metrics = False
    config.observability.components.alerting = False

    errors = validate_configuration(config)

    assert not any("observability" in item for item in errors)


def test_validate_rejects_plain_http_alert_webhook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path, monkeypatch)
    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "http://hooks.example.com/x")
    assert any("ALERT_WEBHOOK_URL must be an https" in item for item in validate_configuration(config))

    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "https://")
    assert any("ALERT_WEBHOOK_URL must be an https" in item for item in validate_configuration(config))
    for broken in ("https://hooks.example.com:notaport/x", "https://hooks.example.com:99999/x", "https://[broken/x"):
        monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", broken)
        assert any("ALERT_WEBHOOK_URL must be an https" in item for item in validate_configuration(config)), broken
    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "https://hooks.example.com:8443/x")
    assert not any("ALERT_WEBHOOK_URL" in item for item in validate_configuration(config))
    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    assert not any("ALERT_WEBHOOK_URL" in item for item in validate_configuration(config))


def test_validate_alert_heartbeat_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _enabled_config(tmp_path, monkeypatch)
    monkeypatch.setenv("EDGE_PLATFORM_ALERT_HEARTBEAT_URL", "https://hc.example.com/ping/x")
    assert any("HEARTBEAT_URL needs EDGE_PLATFORM_ALERT_WEBHOOK_URL" in item for item in validate_configuration(config))

    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    assert not any("HEARTBEAT_URL" in item for item in validate_configuration(config))

    monkeypatch.setenv("EDGE_PLATFORM_ALERT_HEARTBEAT_URL", "http://hc.example.com/ping/x")
    assert any("HEARTBEAT_URL must be an https" in item for item in validate_configuration(config))


def test_validate_requires_grafana_password_only_for_dashboards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _enabled_config(tmp_path, monkeypatch)
    monkeypatch.delenv("EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD")

    assert any("EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD" in item for item in validate_configuration(config))

    config.observability.components.dashboards = False
    assert not any("GRAFANA" in item for item in validate_configuration(config))
