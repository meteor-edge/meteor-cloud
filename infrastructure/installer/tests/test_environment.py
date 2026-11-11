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
    assert extra["demo_data"] == "true"


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


def test_build_ansible_extra_vars_maps_components_to_providers() -> None:
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())
    assert extra["postgres_provider"] == "bundled"
    assert extra["cache_provider"] == "memory"

    config.components.redis.enabled = True
    config.components.postgres.enabled = False
    extra = build_ansible_extra_vars(config, _outputs())
    assert extra["postgres_provider"] == "external"
    assert extra["cache_provider"] == "redis"


def test_build_ansible_extra_vars_passes_external_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "EDGE_PLATFORM_DATABASE_URL",
        "postgresql+psycopg://edge:secret@db.example.com:5432/edge_platform",
    )
    config = load_configuration(EXAMPLE)
    extra = build_ansible_extra_vars(config, _outputs())
    assert extra["external_database_url"].startswith("postgresql+psycopg://")


def test_build_ansible_extra_vars_omits_vpn_key_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY", raising=False)
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())

    assert "vpn_server_private_key" not in extra
    assert "connectivity_provider" not in extra


def test_build_ansible_extra_vars_includes_vpn_key_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY", "super-secret-key")
    config = load_configuration(EXAMPLE)

    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["vpn_server_private_key"] == "super-secret-key"
    # Ansible derives these from the gateway host; extra-vars would override it.
    assert "connectivity_provider" not in extra
    assert "wireguard_server_endpoint" not in extra


def test_build_ansible_extra_vars_dedicated_gateway_link_endpoint() -> None:
    config = load_configuration(EXAMPLE)
    outputs = _outputs(vpn_gateway_public_ip="5.6.7.8", vpn_gateway_private_ip="10.0.0.9")

    extra = build_ansible_extra_vars(config, outputs)

    assert extra["vpn_link_endpoint"] == "10.0.0.9:51820"
    assert "vpn_link_endpoint" not in build_ansible_extra_vars(config, _outputs())

def test_build_ansible_extra_vars_enables_bundled_mqtt() -> None:
    config = load_configuration(EXAMPLE)
    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["mqtt_enabled"] == "true"
    assert extra["mqtt_broker"] == "bundled"
    assert extra["mqtt_broker_port"] == "8883"
    # Bundled keeps the role defaults (emqx in Compose, the platform host for devices).
    assert "mqtt_broker_host" not in extra
    assert "mqtt_public_host" not in extra


def test_build_ansible_extra_vars_external_mqtt_uses_broker_host() -> None:
    config = load_configuration(EXAMPLE)
    config.services.mqtt.broker = "external"
    config.services.mqtt.broker_host = "mqtt.example.net"
    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["mqtt_broker"] == "external"
    assert extra["mqtt_broker_host"] == "mqtt.example.net"
    assert extra["mqtt_public_host"] == "mqtt.example.net"


def test_build_ansible_extra_vars_disables_mqtt() -> None:
    config = load_configuration(EXAMPLE)
    config.services.mqtt.enabled = False
    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["mqtt_enabled"] == "false"
    assert "mqtt_broker" not in extra


def test_build_ansible_extra_vars_passes_observability_components() -> None:
    config = load_configuration(EXAMPLE)
    config.observability.enabled = True
    config.observability.components.dashboards = False
    config.observability.log_retention_hours = 72
    config.observability.metrics_retention_days = 30
    config.observability.metrics_retention_size = "20GB"

    extra = build_ansible_extra_vars(config, _outputs())

    assert extra["observability_components"] == "metrics,logs,alerting"
    assert extra["observability_log_retention"] == "72h"
    assert extra["observability_metrics_retention_time"] == "30d"
    assert extra["observability_metrics_retention_size"] == "20GB"


def test_alert_webhook_url_is_a_secret_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge_installer.deployment.environment import secret_env_vars

    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    config = load_configuration(EXAMPLE)

    assert "observability_alert_webhook_url" not in build_ansible_extra_vars(config, _outputs())
    assert secret_env_vars()["EDGE_PLATFORM_ALERT_WEBHOOK_URL"] == "https://hooks.example.com/x"


def test_alert_heartbeat_url_is_a_secret_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge_installer.deployment.environment import secret_env_vars

    monkeypatch.setenv("EDGE_PLATFORM_ALERT_HEARTBEAT_URL", "https://hc.example.com/ping/x")
    config = load_configuration(EXAMPLE)

    assert "observability_alert_heartbeat_url" not in build_ansible_extra_vars(config, _outputs())
    assert secret_env_vars()["EDGE_PLATFORM_ALERT_HEARTBEAT_URL"] == "https://hc.example.com/ping/x"


def test_grafana_password_reaches_ansible(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge_installer.deployment.environment import secret_env_vars

    monkeypatch.setenv("EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD", "grafana-secret")

    assert secret_env_vars()["EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD"] == "grafana-secret"


def test_secrets_reach_ansible_only_while_the_playbook_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import stat

    from edge_installer.deployment.ansible import AnsibleRunner
    from edge_installer.process.runner import ProcessResult

    monkeypatch.setenv("EDGE_PLATFORM_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> ProcessResult:
        secret_file = tmp_path / "secret-vars.json"
        seen["secrets"] = json.loads(secret_file.read_text(encoding="utf-8"))
        seen["mode"] = stat.S_IMODE(secret_file.stat().st_mode)
        seen["command"] = command
        return ProcessResult(command=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("edge_installer.deployment.ansible.run_command", fake_run)
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[platform]\n", encoding="utf-8")

    AnsibleRunner(ansible_root=tmp_path).run_playbook(
        "site.yml", inventory=inventory, extra_vars={"platform_url": "https://fleet.example.com"}
    )

    assert seen["secrets"] == {"EDGE_PLATFORM_ALERT_WEBHOOK_URL": "https://hooks.example.com/x"}
    assert seen["mode"] == 0o600
    assert f"@{tmp_path / 'secret-vars.json'}" in seen["command"]
    assert not (tmp_path / "secret-vars.json").exists()
    extra_path = tmp_path / "extra-vars.json"
    assert json.loads(extra_path.read_text(encoding="utf-8")) == {"platform_url": "https://fleet.example.com"}
    assert stat.S_IMODE(extra_path.stat().st_mode) == 0o600


def test_secret_vars_file_is_removed_when_ansible_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from edge_installer.deployment.ansible import AnsibleRunner

    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "x" * 32)

    def boom(command: list[str], **kwargs: object) -> None:
        raise OSError("ansible-playbook not found")

    monkeypatch.setattr("edge_installer.deployment.ansible.run_command", boom)
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[platform]\n", encoding="utf-8")

    with pytest.raises(OSError):
        AnsibleRunner(ansible_root=tmp_path).run_playbook("site.yml", inventory=inventory, extra_vars={})
    assert not (tmp_path / "secret-vars.json").exists()
