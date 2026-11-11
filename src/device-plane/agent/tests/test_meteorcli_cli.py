"""Tests for the `meteorcli` command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_agent.client import AgentApiError
from meteorcli import cli as meteorcli
from meteorcli.config import default_config_dir, derive_api_base, lan_host_missing_port
from tests.conftest import FakeClient


@pytest.fixture(autouse=True)
def _no_host_service(monkeypatch, tmp_path_factory):
    """Keep the host's meteorcli.service (or a systemd terminal) out of these tests."""
    from edge_agent import service

    monkeypatch.setattr(service, "running_as_service", lambda: False)
    monkeypatch.setattr(meteorcli, "running_as_service", lambda: False)
    monkeypatch.setattr("meteorcli.config.SERVICE_UNIT", tmp_path_factory.mktemp("nounit") / "meteorcli.service")


def test_derive_api_base() -> None:
    assert derive_api_base("meteorxx.com") == "https://meteorxx.com"
    assert derive_api_base("https://www.meteorxx.com") == "https://meteorxx.com"
    assert derive_api_base("api.meteorxx.com") == "https://api.meteorxx.com"
    assert derive_api_base("meteorxx.com", http=True) == "http://meteorxx.com"
    assert derive_api_base("http://meteorxx.com") == "http://meteorxx.com"
    assert derive_api_base("192.168.0.107") == "http://192.168.0.107"
    assert derive_api_base("192.168.0.107:8000") == "http://192.168.0.107:8000"
    assert derive_api_base("http://192.168.0.107:8000") == "http://192.168.0.107:8000"
    assert derive_api_base("https://api.192.168.0.107") == "http://192.168.0.107"
    assert derive_api_base("https://api.192.168.0.107", http=True) == "http://192.168.0.107"
    assert derive_api_base("localhost:8000") == "http://localhost:8000"


def test_lan_host_missing_port() -> None:
    assert lan_host_missing_port("192.168.0.118") is True
    assert lan_host_missing_port("http://192.168.0.118") is True
    assert lan_host_missing_port("192.168.0.118:8000") is False
    assert lan_host_missing_port("localhost:8000") is False
    assert lan_host_missing_port("demo.meteor-edge.io") is False


def test_config_clears_previous_mqtt_when_domain_changes(tmp_path, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    meteorcli.main(
        ["--config-dir", str(config_dir), "config", "--domain", "demo.meteor-edge.io", "--api-key", "key_old"]
    )
    (config_dir / "mqtt.json").write_text(
        '{"host": "demo.meteor-edge.io", "port": 8883, "username": "u", "password": "p", "tls": true}',
        encoding="utf-8",
    )
    (config_dir / "device-token").write_text("dev_old", encoding="utf-8")
    (config_dir / "mqtt-ca.crt").write_text("CERT", encoding="utf-8")

    code = meteorcli.main(
        ["--config-dir", str(config_dir), "config", "--domain", "192.168.0.118:8000", "--api-key", "key_new"]
    )
    assert code == 0
    out = capsys.readouterr()
    assert "http://192.168.0.118:8000" in out.out
    assert "Cleared previous enrollment" in out.out
    assert not (config_dir / "mqtt.json").exists()
    assert not (config_dir / "device-token").exists()
    assert not (config_dir / "mqtt-ca.crt").exists()


def test_config_server_change_clears_identity_staging_and_api_key(tmp_path, capsys) -> None:
    import json

    config_dir = tmp_path / "meteorcli"
    meteorcli.main(
        ["--config-dir", str(config_dir), "config", "--domain", "demo.meteor-edge.io", "--api-key", "key_old"]
    )
    config_file = config_dir / "config.json"
    data = json.loads(config_file.read_text(encoding="utf-8"))
    data.update(device_id="dev-old", organization_id="org-old")
    config_file.write_text(json.dumps(data), encoding="utf-8")
    staging = config_dir / ".device-secrets-staging"
    staging.mkdir()
    (staging / "device-token").write_text("dev_old", encoding="utf-8")
    (staging / "DONE").write_text("", encoding="utf-8")

    code = meteorcli.main(["--config-dir", str(config_dir), "config", "--domain", "other.meteor-edge.io"])
    assert code == 0
    capsys.readouterr()
    assert not staging.exists()
    assert not (config_dir / "api-key").exists()  # keys belong to the old server
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["device_id"] == "" and saved["organization_id"] == ""
    assert not (config_dir / "device-token").exists()


def test_config_port_hint_follows_the_saved_api_base(tmp_path, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    code = meteorcli.main(
        [
            "--config-dir", str(config_dir), "config",
            "--domain", "192.168.0.118", "--api-base", "http://192.168.0.118:8000", "--api-key", "key_x",
        ]
    )
    assert code == 0
    assert "port 8000" not in capsys.readouterr().err

    code = meteorcli.main(
        ["--config-dir", str(config_dir), "config", "--domain", "lab.example.com", "--api-base", "http://10.0.0.5"]
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "--api-base http://10.0.0.5:8000" in err


def test_config_warns_when_lan_ip_has_no_port(tmp_path, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    code = meteorcli.main(
        ["--config-dir", str(config_dir), "config", "--domain", "192.168.0.118", "--api-key", "key_x"]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "http://192.168.0.118" in captured.out
    assert "port 8000" in captured.err


def test_test_hints_port_when_lan_api_returns_404(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.health_error = AgentApiError(404, "not_found", "Request failed with status 404")
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    meteorcli.main(["--config-dir", str(config_dir), "config", "--domain", "192.168.0.118"])
    code = meteorcli.main(["--config-dir", str(config_dir), "test"])
    assert code == 1
    err = capsys.readouterr().err
    assert "cannot reach" in err
    assert "port 8000" in err


def test_default_config_dir_user(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("meteorcli.config.os.geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_config_dir() == tmp_path / "xdg" / "meteorcli"


def test_default_config_dir_root(monkeypatch) -> None:
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr("meteorcli.config.os.geteuid", lambda: 0)
    assert default_config_dir() == Path("/etc/meteorcli")


def test_sudo_config_uses_invoking_user_dir(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home" / "pi"
    monkeypatch.setattr("meteorcli.config.os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "pi")
    monkeypatch.setattr("meteorcli.config._user_home", lambda username=None: home)
    monkeypatch.setattr("meteorcli.config.SERVICE_UNIT", tmp_path / "missing.service")
    assert default_config_dir() == home / ".config" / "meteorcli"


def test_sudo_config_uses_service_dir_when_service_installed(monkeypatch, tmp_path) -> None:
    # The service reads /etc/meteorcli; enrolling elsewhere left it "not registered".
    unit = tmp_path / "meteorcli.service"
    unit.write_text("[Unit]\n")
    monkeypatch.setattr("meteorcli.config.os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "pi")
    monkeypatch.setattr("meteorcli.config.SERVICE_UNIT", unit)
    assert default_config_dir() == Path("/etc/meteorcli")


def test_user_config_writes_without_root(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("meteorcli.config.os.geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    code = meteorcli.main(
        ["config", "--domain", "192.168.0.107:8000", "--api-key", "key_secret"]
    )
    assert code == 0
    saved = tmp_path / "xdg" / "meteorcli"
    assert (saved / "api-key").read_text(encoding="utf-8") == "key_secret"
    assert "xdg" in capsys.readouterr().out


def test_config_and_show(tmp_path, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "config",
            "--domain",
            "meteorxx.com",
            "--api-key",
            "key_secret",
        ]
    )
    assert code == 0
    assert (config_dir / "api-key").read_text(encoding="utf-8") == "key_secret"
    assert (config_dir / "api-key").stat().st_mode & 0o777 == 0o600

    code = meteorcli.main(["--config-dir", str(config_dir), "config", "--show"])
    assert code == 0
    out = capsys.readouterr().out
    assert "meteorxx.com" in out
    assert "https://meteorxx.com" in out
    assert "API key:        present" in out
    assert "key_secret" not in out


def test_register_status_run_flow(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "register",
            "--server",
            "http://localhost:8000",
            "--token",
            "reg_abc",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Registered device device-1" in out
    assert "dev_secret-value" not in out

    code = meteorcli.main(["--config-dir", str(config_dir), "status"])
    assert code == 0
    status_out = capsys.readouterr().out
    assert "Device ID:      device-1" in status_out
    assert "Credential:     present" in status_out

    code = meteorcli.main(["--config-dir", str(config_dir), "run", "--once"])
    assert code == 0
    assert "status: online" in capsys.readouterr().out


def test_register_uses_configured_domain(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    captured: list[str] = []

    def fake_client(server_url: str, **_: object) -> FakeClient:
        captured.append(server_url)
        client.server_url = server_url.rstrip("/")
        return client

    monkeypatch.setattr(meteorcli, "EdgeClient", fake_client)
    meteorcli.main(["--config-dir", str(config_dir), "config", "--domain", "meteorxx.com"])
    code = meteorcli.main(["--config-dir", str(config_dir), "register", "--token", "reg_abc"])
    assert code == 0
    assert captured[-1] == "https://meteorxx.com"


def test_request_polls_until_approved(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.enroll_poll_responses = [
        {"status": "pending", "poll_interval_seconds": 0},
        {
            "status": "approved",
            "device_id": "device-1",
            "device_token": "dev_secret-value",
            "organization_id": "org-1",
            "name": "edge-01",
            "heartbeat_interval_seconds": 60,
            "poll_interval_seconds": 0,
        },
    ]
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    monkeypatch.setattr(meteorcli.time, "sleep", lambda *_: None)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
            "--name",
            "edge-01",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Registered device device-1" in out
    assert "dev_secret-value" not in out
    assert "clm_secret-value" not in out
    assert (config_dir / "device-token").read_text(encoding="utf-8") == "dev_secret-value"
    assert not (config_dir / "claim-secret").exists()
    assert client.enroll_request_calls[0]["api_key"] == "key_abc"
    assert client.enroll_request_calls[0]["name"] == "edge-01"


def test_request_rejected(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.enroll_poll_responses = [
        {"status": "rejected", "rejection_reason": "Unknown hardware", "poll_interval_seconds": 0}
    ]
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    monkeypatch.setattr(meteorcli.time, "sleep", lambda *_: None)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
        ]
    )
    assert code == 1
    assert "Unknown hardware" in capsys.readouterr().err


def test_request_times_out_and_keeps_claim_secret(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.enroll_poll_responses = [
        {"status": "pending", "poll_interval_seconds": 10},
        {"status": "pending", "poll_interval_seconds": 10},
    ]
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    monkeypatch.setattr(meteorcli.time, "sleep", lambda *_: None)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
            "--wait",
            "10",
        ]
    )
    assert code == 2
    out = capsys.readouterr().out
    assert "meteorcli claim" in out
    claim = (config_dir / "claim-secret").read_text(encoding="utf-8")
    assert "req-1" in claim
    assert "clm_secret-value" in claim
    assert len(client.enroll_poll_calls) <= 2


def test_request_refuses_second_submit_while_pending(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    config_dir.mkdir()
    (config_dir / "claim-secret").write_text(
        '{"request_id": "req-1", "claim_secret": "clm_secret-value"}',
        encoding="utf-8",
    )
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
        ]
    )
    assert code == 2
    assert "pending enrollment request already exists" in capsys.readouterr().err
    assert client.enroll_request_calls == []
    assert client.enroll_poll_calls == [
        {"request_id": "req-1", "claim_secret": "clm_secret-value"}
    ]


def test_request_after_rejection_submits_again(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    config_dir.mkdir()
    (config_dir / "claim-secret").write_text(
        '{"request_id": "req-1", "claim_secret": "clm_secret-value"}',
        encoding="utf-8",
    )
    client = FakeClient()
    client.enroll_poll_responses = [
        {"status": "rejected", "rejection_reason": "Unknown hardware", "poll_interval_seconds": 0}
    ]
    client.enroll_request_response = {
        "request_id": "req-2",
        "claim_secret": "clm_new-secret",
        "status": "pending",
        "poll_interval_seconds": 1,
        "expires_at": None,
    }
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
            "--wait",
            "0",
        ]
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "Previous enrollment request was rejected" in err
    assert client.enroll_request_calls
    claim = (config_dir / "claim-secret").read_text(encoding="utf-8")
    assert "req-2" in claim


def test_request_alias_still_works(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.enroll_poll_responses = [
        {
            "status": "approved",
            "device_id": "device-1",
            "device_token": "dev_secret-value",
            "organization_id": "org-1",
            "name": "edge-01",
            "heartbeat_interval_seconds": 60,
            "poll_interval_seconds": 0,
        }
    ]
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    monkeypatch.setattr(meteorcli.time, "sleep", lambda *_: None)

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
            "--wait",
            "0",
        ]
    )
    assert code == 0
    assert len(client.enroll_request_calls) == 1


def test_claim_collects_later_approval(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        '{"server_url": "http://localhost:8000"}',
        encoding="utf-8",
    )
    (config_dir / "claim-secret").write_text(
        '{"request_id": "req-1", "claim_secret": "clm_secret-value"}',
        encoding="utf-8",
    )
    client = FakeClient()
    client.enroll_poll_responses = [
        {
            "status": "approved",
            "device_id": "device-1",
            "device_token": "dev_secret-value",
            "organization_id": "org-1",
            "name": "edge-01",
            "heartbeat_interval_seconds": 60,
            "poll_interval_seconds": 10,
        }
    ]
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)

    code = meteorcli.main(["--config-dir", str(config_dir), "claim"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Registered device device-1" in out
    assert (config_dir / "device-token").read_text(encoding="utf-8") == "dev_secret-value"
    assert not (config_dir / "claim-secret").exists()
    assert client.enroll_poll_calls == [{"request_id": "req-1", "claim_secret": "clm_secret-value"}]


def test_config_http_ip_uses_plain_http(tmp_path, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "config",
            "--domain",
            "192.168.0.107:8000",
            "--api-key",
            "key_secret",
        ]
    )
    assert code == 0
    code = meteorcli.main(["--config-dir", str(config_dir), "config", "--show"])
    assert code == 0
    out = capsys.readouterr().out
    assert "http://192.168.0.107:8000" in out
    assert "api.192.168.0.107" not in out


def test_old_api_subdomain_ip_connects_to_the_ip(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "meteorcli"
    captured: list[str] = []
    client = FakeClient()

    def fake_client(server_url: str, **_: object) -> FakeClient:
        captured.append(server_url)
        return client

    monkeypatch.setattr(meteorcli, "EdgeClient", fake_client)
    meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "config",
            "--api-base",
            "https://api.192.168.0.107:8000",
            "--api-key",
            "key_secret",
        ]
    )
    code = meteorcli.main(["--config-dir", str(config_dir), "test"])
    assert code == 0
    assert captured[-1] == "http://192.168.0.107:8000"


def test_test_http_flag_rewrites_https_ip(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    captured: list[str] = []
    client = FakeClient()

    def fake_client(server_url: str, **_: object) -> FakeClient:
        captured.append(server_url)
        return client

    monkeypatch.setattr(meteorcli, "EdgeClient", fake_client)
    meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "config",
            "--api-base",
            "https://api.192.168.0.107:8000",
            "--api-key",
            "key_secret",
        ]
    )
    code = meteorcli.main(["--config-dir", str(config_dir), "test", "--http"])
    assert code == 0
    assert captured[-1] == "http://192.168.0.107:8000"
    code = meteorcli.main(["--config-dir", str(tmp_path / "empty"), "status"])
    assert code == 1
    assert "Not configured" in capsys.readouterr().out


def test_connection_success(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)

    meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "config",
            "--domain",
            "meteorxx.com",
            "--api-key",
            "key_secret",
        ]
    )
    code = meteorcli.main(["--config-dir", str(config_dir), "test"])
    assert code == 0
    out = capsys.readouterr().out
    assert "https://meteorxx.com" in out
    assert "Server:         ok" in out
    assert "API key:        ok" in out
    assert "Acme Energy" in out
    assert "key_secret" not in out
    assert client.check_calls[0]["api_key"] == "key_secret"


def test_connection_unreachable(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.health_error = AgentApiError(0, "network_error", "connection refused")
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    meteorcli.main(["--config-dir", str(config_dir), "config", "--domain", "meteorxx.com"])

    code = meteorcli.main(["--config-dir", str(config_dir), "test"])
    assert code == 1
    assert "cannot reach" in capsys.readouterr().err
    assert client.check_calls == []


def test_connection_rejects_invalid_key(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    client.check_error = AgentApiError(401, "invalid_api_key", "The enrollment API key is invalid.")
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "config",
            "--domain",
            "meteorxx.com",
            "--api-key",
            "key_bad",
        ]
    )
    code = meteorcli.main(["--config-dir", str(config_dir), "test"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Server:         ok" in captured.out
    assert "invalid" in captured.err


def test_connection_requires_api_key(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    meteorcli.main(["--config-dir", str(config_dir), "config", "--domain", "meteorxx.com"])

    code = meteorcli.main(["--config-dir", str(config_dir), "test"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Server:         ok" in captured.out
    assert "API key is required" in captured.err


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        meteorcli.main(["--version"])
    assert excinfo.value.code == 0
    assert "meteorcli" in capsys.readouterr().out


def test_help_lists_commands(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        meteorcli.main(["--help"])
    assert excinfo.value.code == 0
    help_out = capsys.readouterr().out
    assert "register" in help_out
    assert "request-token" in help_out
    assert "claim" in help_out
    assert "config" in help_out
    assert "test" in help_out
    assert "run" in help_out
    assert "mqtt-test" in help_out
    assert "mqtt-listen" in help_out
    assert "status" in help_out


def test_mqtt_test_requires_mqtt_json(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    assert meteorcli.main(
        ["--config-dir", str(config_dir), "register", "--server", "http://localhost:8000", "--token", "reg_abc"]
    ) == 0
    capsys.readouterr()
    code = meteorcli.main(["--config-dir", str(config_dir), "mqtt-test"])
    assert code == 1
    assert "MQTT is not configured" in capsys.readouterr().err


def test_mqtt_test_publishes_status_topic(tmp_path, monkeypatch, capsys) -> None:
    from edge_agent.mqtt_config import MqttConfig, write_mqtt_config

    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    assert meteorcli.main(
        ["--config-dir", str(config_dir), "register", "--server", "http://localhost:8000", "--token", "reg_abc"]
    ) == 0
    write_mqtt_config(
        config_dir,
        MqttConfig(host="localhost", port=8883, username="u", password="p", tls=True),
    )
    published: list[tuple[str, dict | str, object]] = []

    def fake_publish(device_id: str, _config: object, payload: dict | str, **kwargs: object) -> str:
        topic = str(kwargs.get("topic") or f"devices/{device_id}/status")
        published.append((device_id, payload, topic))
        return topic

    monkeypatch.setattr("edge_agent.mqtt.publish_test_event", fake_publish)
    capsys.readouterr()
    code = meteorcli.main(["--config-dir", str(config_dir), "mqtt-test"])
    assert code == 0
    assert published[0][0] == "device-1"
    assert published[0][1]["message"] == "mqtt-test"
    assert published[0][1]["status"] == "online"
    assert published[0][2] == "devices/device-1/status"
    assert "devices/device-1/status" in capsys.readouterr().out

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "mqtt-test",
            "metrics",
            '{"cpu_percent":1}',
        ]
    )
    assert code == 0
    assert published[1][1] == {"cpu_percent": 1}
    assert published[1][2] == "devices/device-1/metrics"
    assert "devices/device-1/metrics" in capsys.readouterr().out

    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "mqtt-test",
            "custom",
            '{"hello":true}',
        ]
    )
    assert code == 1
    assert "must be one of" in capsys.readouterr().err


def test_mqtt_test_rejects_other_device_topic(tmp_path, monkeypatch, capsys) -> None:
    from edge_agent.mqtt_config import MqttConfig, write_mqtt_config

    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    assert meteorcli.main(
        ["--config-dir", str(config_dir), "register", "--server", "http://localhost:8000", "--token", "reg_abc"]
    ) == 0
    write_mqtt_config(
        config_dir,
        MqttConfig(host="localhost", port=8883, username="u", password="p", tls=True),
    )
    capsys.readouterr()
    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "mqtt-test",
            "devices/11111111-1111-1111-1111-111111111111/events",
        ]
    )
    assert code == 1
    assert "must be under" in capsys.readouterr().err


def test_mqtt_listen_requires_mqtt_json(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    assert meteorcli.main(
        ["--config-dir", str(config_dir), "register", "--server", "http://localhost:8000", "--token", "reg_abc"]
    ) == 0
    capsys.readouterr()
    code = meteorcli.main(["--config-dir", str(config_dir), "mqtt-listen", "--timeout", "0"])
    assert code == 1
    assert "MQTT is not configured" in capsys.readouterr().err


def test_mqtt_listen_subscribes_to_commands_topic(tmp_path, monkeypatch, capsys) -> None:
    from edge_agent.mqtt_config import MqttConfig, write_mqtt_config

    config_dir = tmp_path / "meteorcli"
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    assert meteorcli.main(
        ["--config-dir", str(config_dir), "register", "--server", "http://localhost:8000", "--token", "reg_abc"]
    ) == 0
    write_mqtt_config(
        config_dir,
        MqttConfig(host="localhost", port=8883, username="u", password="p", tls=True),
    )
    listened: list[tuple[str, object, float | None]] = []

    def fake_listen(device_id: str, config: object, **kwargs: object) -> str:
        topic = str(kwargs.get("topic") or f"devices/{device_id}/commands")
        listened.append((device_id, config, kwargs.get("timeout"), topic))
        kwargs["on_message"](topic, '{"hello":true}')  # type: ignore[operator]
        return topic

    monkeypatch.setattr("edge_agent.mqtt.listen_mqtt", fake_listen)
    capsys.readouterr()
    code = meteorcli.main(["--config-dir", str(config_dir), "mqtt-listen", "--timeout", "0"])
    assert code == 0
    assert listened[0][0] == "device-1"
    assert listened[0][2] == 0.0
    assert listened[0][3] == "devices/device-1/commands"
    out = capsys.readouterr().out
    assert "devices/device-1/commands" in out
    assert '{"hello":true}' in out

    code = meteorcli.main(
        ["--config-dir", str(config_dir), "mqtt-listen", "commands", "--timeout", "0"]
    )
    assert code == 0
    assert listened[1][3] == "devices/device-1/commands"
    assert "devices/device-1/commands" in capsys.readouterr().out


def _as_service(monkeypatch) -> None:
    """Pretend this process is meteorcli.service (it reads its own cgroup)."""
    from edge_agent import service

    monkeypatch.setattr(service, "running_as_service", lambda: True)
    monkeypatch.setattr(meteorcli, "running_as_service", lambda: True)


def _enroll(tmp_path, monkeypatch):
    from edge_agent.config import AgentConfig, AgentPaths, save_config
    from edge_agent.credentials import write_device_token

    config_dir = tmp_path / "meteorcli"
    paths = AgentPaths(config_path=config_dir / "config.json", token_path=config_dir / "device-token")
    save_config(
        paths,
        AgentConfig(
            server_url="http://localhost:8000",
            device_id="device-1",
            organization_id="org-1",
            name="edge-01",
            heartbeat_interval_seconds=60,
        ),
    )
    write_device_token(paths.token_path, "dev_secret-value")
    client = FakeClient()
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    return config_dir, client


def test_status_shows_service_and_wireguard(tmp_path, monkeypatch, capsys) -> None:
    from edge_agent.wireguard import TunnelStatus

    config_dir, _client = _enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr(
        "edge_agent.wireguard.describe_tunnel",
        lambda _dir: TunnelStatus(
            available=True,
            configured=True,
            up=True,
            address="10.8.0.12/32",
            handshake_age_seconds=8,
        ),
    )
    code = meteorcli.main(["--config-dir", str(config_dir), "status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Service:        meteorcli.service is active" in out
    assert "WireGuard:      WireGuard wg0 up, 10.8.0.12/32, handshake 8s ago" in out


def test_run_refuses_when_service_already_active(tmp_path, monkeypatch, capsys) -> None:
    config_dir, _client = _enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    ran = []
    monkeypatch.setattr(meteorcli, "run_loop", lambda *_a, **_k: ran.append("loop"))
    code = meteorcli.main(["--config-dir", str(config_dir), "run"])
    assert code == 1
    assert ran == []
    err = capsys.readouterr().err
    assert "meteorcli.service is already running" in err
    assert "journalctl -u meteorcli -f" in err


def test_run_once_allowed_when_service_active(tmp_path, monkeypatch, capsys) -> None:
    from edge_agent.wireguard import TunnelStatus

    config_dir, _client = _enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr("edge_agent.wireguard.sync_connectivity", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "edge_agent.wireguard.describe_tunnel",
        lambda _dir: TunnelStatus(available=True, configured=False, up=False),
    )
    monkeypatch.setattr(meteorcli, "reconcile_software", lambda *_a, **_k: [])
    monkeypatch.setattr(meteorcli, "collect_capabilities", lambda: {})
    code = meteorcli.main(["--config-dir", str(config_dir), "run", "--once"])
    assert code == 0
    out = capsys.readouterr().out
    assert "status: online" in out
    assert "WireGuard not configured" in out


def test_run_proceeds_when_this_process_is_the_service(tmp_path, monkeypatch) -> None:
    config_dir, _client = _enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    _as_service(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(meteorcli, "run_loop", lambda *_a, **_k: ran.append("loop"))
    monkeypatch.setattr("edge_agent.mqtt_sync.MqttSupervisor", lambda *_a, **_k: None)
    code = meteorcli.main(["--config-dir", str(config_dir), "run"])
    assert code == 0
    assert ran == ["loop"]


def test_logs_lists_sources_as_json(monkeypatch, capsys) -> None:
    from edge_agent import logs

    monkeypatch.setattr(logs, "helper_installed", lambda: True)
    monkeypatch.setattr(logs, "list_log_sources", lambda: [{"kind": "journal", "name": ""}])
    assert meteorcli.main(["logs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"kind": "journal", "name": ""}]


def test_logs_runs_helper_with_source_and_options(monkeypatch) -> None:
    import subprocess

    from edge_agent import logs

    monkeypatch.setattr(logs, "helper_installed", lambda: True)
    monkeypatch.setattr(logs.os, "geteuid", lambda: 1000)
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_k: seen.append(argv) or subprocess.CompletedProcess(argv, 0))
    code = meteorcli.main(["logs", "systemd:nginx.service", "-n", "50", "--grep", "err", "-f"])
    assert code == 0
    assert seen == [
        [
            "sudo",
            "-n",
            logs.LOGS_HELPER,
            "show",
            "systemd",
            "nginx.service",
            "--lines",
            "50",
            "--grep",
            "err",
            "--follow",
        ]
    ]


def test_logs_without_helper_explains_install(monkeypatch, capsys) -> None:
    from edge_agent import logs

    monkeypatch.setattr(logs, "helper_installed", lambda: False)
    assert meteorcli.main(["logs"]) == 1
    assert "installcli.sh" in capsys.readouterr().err


def test_run_reowns_the_config_dir_after_reconcile(tmp_path, monkeypatch, capsys) -> None:
    from edge_agent.wireguard import TunnelStatus

    config_dir, _client = _enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "inactive")
    monkeypatch.setattr("edge_agent.wireguard.sync_connectivity", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "edge_agent.wireguard.describe_tunnel",
        lambda _dir: TunnelStatus(available=True, configured=False, up=False),
    )
    events: list[str] = []
    monkeypatch.setattr(meteorcli, "reconcile_software", lambda *_a, **_k: events.append("reconcile") or [])
    monkeypatch.setattr(meteorcli, "chown_config_dir", lambda _path: events.append("chown"))
    monkeypatch.setattr(meteorcli, "collect_capabilities", lambda: {})
    assert meteorcli.main(["--config-dir", str(config_dir), "run", "--once"]) == 0
    capsys.readouterr()
    assert events[-2:] == ["reconcile", "chown"]


# --- meteorcli run waits for a pending token request (first boot hands the wait to the service) ---

_APPROVED = {
    "status": "approved",
    "device_id": "device-1",
    "device_token": "dev_secret-value",
    "organization_id": "org-1",
    "name": "edge-01",
    "heartbeat_interval_seconds": 60,
    "poll_interval_seconds": 10,
}


def _pending_device(tmp_path: Path, *, api_key: str | None = "key_abc", name: str | None = "edge-01") -> Path:
    config_dir = tmp_path / "meteorcli"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"server_url": "http://localhost:8000"}', encoding="utf-8")
    claim = {"request_id": "req-1", "claim_secret": "clm_secret-value"}
    if name:
        claim["name"] = name
    (config_dir / "claim-secret").write_text(json.dumps(claim), encoding="utf-8")
    if api_key:
        (config_dir / "api-key").write_text(api_key, encoding="utf-8")
    return config_dir


@pytest.fixture
def run_env(monkeypatch):
    """Fake client, no real sleeps, no systemd, and a run loop that returns at once."""
    client = FakeClient()
    sleeps: list[float] = []
    loops: list[dict] = []
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)
    monkeypatch.setattr(meteorcli.time, "sleep", sleeps.append)
    monkeypatch.setattr(meteorcli, "service_state", lambda: meteorcli.NOT_INSTALLED)
    monkeypatch.setattr(meteorcli, "collect_inventory", lambda: {"hostname": "pi"})
    monkeypatch.setattr(
        meteorcli,
        "run_loop",
        lambda client, token, **kwargs: loops.append({"token": token, **kwargs}),
    )
    return client, sleeps, loops


def test_run_waits_for_approval_then_runs(tmp_path, run_env) -> None:
    client, sleeps, loops = run_env
    config_dir = _pending_device(tmp_path)
    client.enroll_poll_responses = [
        {"status": "pending", "poll_interval_seconds": 10},
        {"status": "pending", "poll_interval_seconds": 10},
        dict(_APPROVED),
    ]

    code = meteorcli.main(["--config-dir", str(config_dir), "run"])

    assert code == 0
    assert len(client.enroll_poll_calls) == 3
    assert sleeps == [10, 10]
    assert (config_dir / "device-token").read_text(encoding="utf-8") == "dev_secret-value"
    assert not (config_dir / "claim-secret").exists()
    assert loops and loops[0]["token"] == "dev_secret-value"
    assert client.enroll_request_calls == []


def test_run_never_polls_faster_than_minimum(tmp_path, run_env) -> None:
    client, sleeps, _ = run_env
    config_dir = _pending_device(tmp_path)
    client.enroll_poll_responses = [{"status": "pending", "poll_interval_seconds": 1}, dict(_APPROVED)]

    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert sleeps == [meteorcli.MIN_POLL_INTERVAL_SECONDS]


def test_run_keeps_waiting_through_network_errors(tmp_path, run_env, monkeypatch) -> None:
    client, sleeps, loops = run_env
    config_dir = _pending_device(tmp_path)
    responses = [AgentApiError(0, "network_error", "down"), dict(_APPROVED)]

    def poll(**kwargs):
        client.enroll_poll_calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client, "enroll_poll", poll)
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert sleeps == [meteorcli.RATE_LIMIT_BACKOFF_SECONDS]
    assert loops


def test_run_resubmits_expired_request_with_stored_key(tmp_path, run_env, caplog) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)
    client.enroll_request_response = {
        "request_id": "req-2",
        "claim_secret": "clm_second",
        "status": "pending",
        "poll_interval_seconds": 10,
    }
    client.enroll_poll_responses = [{"status": "expired", "poll_interval_seconds": 10}, dict(_APPROVED)]

    with caplog.at_level("INFO", logger="meteorcli"):
        assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0

    assert client.enroll_request_calls == [{"api_key": "key_abc", "inventory": {"hostname": "pi"}, "name": "edge-01"}]
    assert client.enroll_poll_calls[1] == {"request_id": "req-2", "claim_secret": "clm_second"}
    assert "key_abc" not in caplog.text and "clm_" not in caplog.text
    assert loops


def test_run_expired_without_api_key_stops(tmp_path, run_env, capsys) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path, api_key=None)
    client.enroll_poll_responses = [{"status": "expired", "poll_interval_seconds": 10}]

    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 1
    assert "no API key is stored" in capsys.readouterr().err
    assert not (config_dir / "claim-secret").exists()
    assert client.enroll_request_calls == []
    assert loops == []


def test_run_rejected_request_stops(tmp_path, run_env, capsys) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)
    client.enroll_poll_responses = [{"status": "rejected", "rejection_reason": "unknown device"}]

    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 1
    assert "unknown device" in capsys.readouterr().err
    assert not (config_dir / "claim-secret").exists()
    assert loops == []


def test_run_unknown_request_drops_claim(tmp_path, run_env, monkeypatch) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)

    def poll(**_):
        raise AgentApiError(401, "invalid_enrollment_request", "The enrollment request is invalid.")

    monkeypatch.setattr(client, "enroll_poll", poll)
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 1
    assert not (config_dir / "claim-secret").exists()
    assert loops == []


def test_run_once_does_not_wait_for_pending_request(tmp_path, run_env, capsys) -> None:
    client, _, _ = run_env
    config_dir = _pending_device(tmp_path)

    assert meteorcli.main(["--config-dir", str(config_dir), "run", "--once"]) == 1
    assert client.enroll_poll_calls == []
    assert "not registered" in capsys.readouterr().err


def test_request_token_stores_name_for_resubmit(tmp_path, run_env) -> None:
    config_dir = tmp_path / "meteorcli"
    code = meteorcli.main(
        [
            "--config-dir",
            str(config_dir),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
            "--name",
            "pi-07",
            "--wait",
            "0",
        ]
    )
    assert code == 0
    stored = json.loads((config_dir / "claim-secret").read_text(encoding="utf-8"))
    assert stored == {"request_id": "req-1", "claim_secret": "clm_secret-value", "name": "pi-07"}


# --- meteorcli.service: wait for setup and registration instead of exiting ---------------


def _write_enrolled(config_dir: Path) -> None:
    """What `meteorcli register` (or another claimer) leaves behind."""
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"server_url": "http://localhost:8000", "device_id": "device-1", "organization_id": "org-1"}),
        encoding="utf-8",
    )
    (config_dir / "device-token").write_text("dev_secret-value", encoding="utf-8")


def _scripted_sleep(monkeypatch, steps):
    """Replace time.sleep; each call runs the next step (to change the config dir), then records."""
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if not steps:
            raise AssertionError("the service kept waiting after the script ran out")
        steps.pop(0)()

    monkeypatch.setattr(meteorcli.time, "sleep", fake_sleep)
    return sleeps


def test_service_idles_until_configured_then_waits_for_approval(tmp_path, run_env, monkeypatch, caplog) -> None:
    client, _, loops = run_env
    _as_service(monkeypatch)
    config_dir = tmp_path / "meteorcli"
    client.enroll_poll_responses = [{"status": "pending", "poll_interval_seconds": 10}, dict(_APPROVED)]

    def configure():
        pass  # still nothing on disk

    def request_token():
        _pending_device(tmp_path)

    sleeps = _scripted_sleep(monkeypatch, [configure, request_token, lambda: None])

    with caplog.at_level("INFO", logger="meteorcli"):
        assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0

    assert sleeps == [meteorcli.SERVICE_IDLE_CHECK_SECONDS, meteorcli.SERVICE_IDLE_CHECK_SECONDS, 10]
    assert caplog.text.count("Waiting for setup") == 1
    assert loops and loops[0]["token"] == "dev_secret-value"


def test_service_announces_registration_wait_once(tmp_path, run_env, monkeypatch, caplog) -> None:
    _, _, loops = run_env
    _as_service(monkeypatch)
    config_dir = tmp_path / "meteorcli"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"server_url": "http://localhost:8000"}', encoding="utf-8")
    _scripted_sleep(monkeypatch, [lambda: None, lambda: None, lambda: _write_enrolled(config_dir)])

    with caplog.at_level("INFO", logger="meteorcli"):
        assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0

    assert caplog.text.count("Waiting for registration") == 1
    assert loops


def test_service_rejected_request_goes_back_to_idle(tmp_path, run_env, monkeypatch, capsys) -> None:
    client, _, loops = run_env
    _as_service(monkeypatch)
    config_dir = _pending_device(tmp_path)
    client.enroll_poll_responses = [{"status": "rejected", "rejection_reason": "unknown device"}]
    sleeps = _scripted_sleep(monkeypatch, [lambda: _write_enrolled(config_dir)])

    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0

    assert "unknown device" in capsys.readouterr().err
    assert sleeps == [meteorcli.SERVICE_IDLE_CHECK_SECONDS]
    assert loops


def test_service_stops_polling_when_another_process_claimed(tmp_path, run_env, monkeypatch) -> None:
    client, _, loops = run_env
    _as_service(monkeypatch)
    config_dir = _pending_device(tmp_path)
    client.enroll_poll_responses = [{"status": "pending", "poll_interval_seconds": 10}]

    def interactive_claim():
        _write_enrolled(config_dir)
        (config_dir / "claim-secret").unlink()

    _scripted_sleep(monkeypatch, [interactive_claim])

    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert len(client.enroll_poll_calls) == 1
    assert loops


def test_wait_treats_approved_without_token_as_done_when_enrolled(tmp_path, run_env, monkeypatch) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)

    def poll(**kwargs):
        # The interactive `claim` collected the token between our disk check and this poll.
        client.enroll_poll_calls.append(kwargs)
        _write_enrolled(config_dir)
        return {"status": "approved", "device_token": None}

    monkeypatch.setattr(client, "enroll_poll", poll)
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert loops and loops[0]["token"] == "dev_secret-value"


def test_wait_follows_a_replaced_request(tmp_path, run_env, monkeypatch) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)
    client.enroll_poll_responses = [{"status": "pending", "poll_interval_seconds": 10}, dict(_APPROVED)]

    def request_token_new():
        (config_dir / "claim-secret").write_text(
            json.dumps({"request_id": "req-9", "claim_secret": "clm_new"}), encoding="utf-8"
        )

    _scripted_sleep(monkeypatch, [request_token_new])
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert client.enroll_poll_calls[1] == {"request_id": "req-9", "claim_secret": "clm_new"}
    assert client.enroll_request_calls == []
    assert loops


def _request_token_args(config_dir: Path, *extra: str) -> list[str]:
    return [
        "--config-dir",
        str(config_dir),
        "request-token",
        "--server",
        "http://localhost:8000",
        "--api-key",
        "key_abc",
        *extra,
    ]


def _saved_server(config_dir: Path, server: str = "http://localhost:8000") -> None:
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({"server_url": server}), encoding="utf-8")


def test_request_token_leaves_the_wait_to_the_running_service(tmp_path, run_env, monkeypatch, capsys) -> None:
    client, _, _ = run_env
    config_dir = tmp_path / "meteorcli"
    _saved_server(config_dir)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr("meteorcli.config.SYSTEM_CONFIG_DIR", config_dir)

    assert meteorcli.main(_request_token_args(config_dir)) == 0

    assert client.enroll_poll_calls == []
    out = capsys.readouterr().out
    assert "meteorcli.service is waiting for an administrator" in out
    assert (config_dir / "claim-secret").exists()


def test_request_token_explicit_wait_still_polls_with_service(tmp_path, run_env, monkeypatch) -> None:
    client, _, _ = run_env
    config_dir = tmp_path / "meteorcli"
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr("meteorcli.config.SYSTEM_CONFIG_DIR", config_dir)
    client.enroll_poll_responses = [dict(_APPROVED)]

    assert meteorcli.main(_request_token_args(config_dir, "--wait", "60")) == 0
    assert len(client.enroll_poll_calls) == 1


def test_request_token_waits_when_service_reads_another_dir(tmp_path, run_env, monkeypatch, capsys) -> None:
    client, _, _ = run_env
    config_dir = tmp_path / "meteorcli"
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr("meteorcli.config.SYSTEM_CONFIG_DIR", tmp_path / "etc-meteorcli")
    client.enroll_poll_responses = [dict(_APPROVED)]

    assert meteorcli.main(_request_token_args(config_dir)) == 0
    assert len(client.enroll_poll_calls) == 1
    assert "reads" in capsys.readouterr().out


def test_non_root_uses_service_dir_when_service_installed(monkeypatch, tmp_path) -> None:
    unit = tmp_path / "meteorcli.service"
    unit.write_text("[Unit]\n")
    monkeypatch.setattr("meteorcli.config.os.geteuid", lambda: 1000)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr("meteorcli.config.SERVICE_UNIT", unit)
    assert default_config_dir() == Path("/etc/meteorcli")


def test_permission_error_suggests_sudo(tmp_path, monkeypatch, capsys) -> None:
    def denied(_args):
        raise PermissionError(13, "Permission denied", "/etc/meteorcli/device-token")

    monkeypatch.setattr(meteorcli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(meteorcli, "_cmd_status", denied)
    assert meteorcli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "permission denied: /etc/meteorcli/device-token" in err
    assert "sudo meteorcli status" in err


def test_claim_succeeds_when_the_service_collected_the_token(tmp_path, run_env, capsys) -> None:
    client, _, _ = run_env
    config_dir = _pending_device(tmp_path)
    _write_enrolled(config_dir)
    (config_dir / "config.json").write_text(
        json.dumps({"server_url": "http://localhost:8000", "device_id": "device-1"}), encoding="utf-8"
    )
    client.enroll_poll_responses = [{"status": "approved", "device_token": None}]

    assert meteorcli.main(["--config-dir", str(config_dir), "claim"]) == 0
    assert "collected by meteorcli.service" in capsys.readouterr().out
    assert not (config_dir / "claim-secret").exists()


def test_request_token_waits_when_the_service_is_already_connected(tmp_path, run_env, monkeypatch) -> None:
    # A connected service is in its heartbeat loop and no longer looks at claims.
    client, _, _ = run_env
    config_dir = tmp_path / "meteorcli"
    _write_enrolled(config_dir)
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr("meteorcli.config.SYSTEM_CONFIG_DIR", config_dir)
    client.enroll_poll_responses = [dict(_APPROVED)]

    assert meteorcli.main(_request_token_args(config_dir)) == 0
    assert len(client.enroll_poll_calls) == 1


def test_request_token_waits_when_server_differs_from_the_saved_one(tmp_path, run_env, monkeypatch) -> None:
    # The service polls the saved server; a request sent elsewhere would never be collected.
    client, _, _ = run_env
    config_dir = tmp_path / "meteorcli"
    _saved_server(config_dir, "http://other-cloud:8000")
    monkeypatch.setattr(meteorcli, "service_state", lambda: "active")
    monkeypatch.setattr("meteorcli.config.SYSTEM_CONFIG_DIR", config_dir)
    client.enroll_poll_responses = [dict(_APPROVED)]

    assert meteorcli.main(_request_token_args(config_dir)) == 0
    assert len(client.enroll_poll_calls) == 1


def test_service_does_not_spin_on_a_token_without_device_id(tmp_path, run_env, monkeypatch) -> None:
    # Interrupted claim: the token was committed, the config with device_id was not.
    client, _, loops = run_env
    _as_service(monkeypatch)
    config_dir = _pending_device(tmp_path)
    (config_dir / "device-token").write_text("dev_secret-value", encoding="utf-8")
    client.enroll_poll_responses = [{"status": "approved", "device_token": None}]
    sleeps = _scripted_sleep(monkeypatch, [lambda: _write_enrolled(config_dir)])

    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert sleeps == [meteorcli.SERVICE_IDLE_CHECK_SECONDS]
    assert loops


def test_unknown_old_request_does_not_drop_a_replacement(tmp_path, run_env, monkeypatch) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)
    answers = ["invalid", dict(_APPROVED)]

    def poll(**kwargs):
        client.enroll_poll_calls.append(kwargs)
        answer = answers.pop(0)
        if answer == "invalid":
            # `request-token --new` replaced the claim while this poll was in flight.
            (config_dir / "claim-secret").write_text(
                json.dumps({"request_id": "req-9", "claim_secret": "clm_new"}), encoding="utf-8"
            )
            raise AgentApiError(404, "not_found", "Unknown enrollment request.")
        return answer

    monkeypatch.setattr(client, "enroll_poll", poll)
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert client.enroll_poll_calls[1] == {"request_id": "req-9", "claim_secret": "clm_new"}
    assert loops


def test_expired_old_request_is_not_resubmitted_over_a_replacement(tmp_path, run_env, monkeypatch) -> None:
    client, _, loops = run_env
    config_dir = _pending_device(tmp_path)
    answers = [{"status": "expired"}, dict(_APPROVED)]

    def poll(**kwargs):
        client.enroll_poll_calls.append(kwargs)
        if len(client.enroll_poll_calls) == 1:
            (config_dir / "claim-secret").write_text(
                json.dumps({"request_id": "req-9", "claim_secret": "clm_new"}), encoding="utf-8"
            )
        return answers.pop(0)

    monkeypatch.setattr(client, "enroll_poll", poll)
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert client.enroll_request_calls == []
    assert client.enroll_poll_calls[1]["request_id"] == "req-9"
    assert loops


@pytest.mark.parametrize(
    ("error", "expected", "retry_line"),
    [
        (AgentApiError(401, "invalid_api_key", "The enrollment API key has expired."), 3, None),
        (AgentApiError(403, "forbidden", "Forbidden"), 3, None),
        (AgentApiError(429, "rate_limited", "Too many", retry_after=42), 75, "retry-after: 42"),
        (AgentApiError(0, "network_error", "Name or service not known"), 75, None),
        (AgentApiError(503, "unavailable", "Down"), 75, None),
        (AgentApiError(422, "validation_error", "bad"), 1, None),
    ],
)
def test_request_token_exit_code_says_whether_to_retry(
    tmp_path, monkeypatch, capsys, error, expected, retry_line
) -> None:
    client = FakeClient()
    client.enroll_request_error = error
    monkeypatch.setattr(meteorcli, "EdgeClient", lambda server_url, **_: client)

    code = meteorcli.main(
        [
            "--config-dir",
            str(tmp_path / "meteorcli"),
            "request-token",
            "--server",
            "http://localhost:8000",
            "--api-key",
            "key_abc",
            "--wait",
            "0",
        ]
    )

    assert code == expected
    err = capsys.readouterr().err
    if retry_line:
        assert retry_line in err
    else:
        assert "retry-after:" not in err
    assert not (tmp_path / "meteorcli" / "claim-secret").exists()


# --- meteorcli.service never exits -------------------------------------------------------


def test_service_stays_up_when_the_credential_is_refused_and_uses_a_new_one(tmp_path, run_env, monkeypatch, caplog):
    client, sleeps, _ = run_env
    _as_service(monkeypatch)
    config_dir = tmp_path / "meteorcli"
    _write_enrolled(config_dir)
    tokens: list[str] = []

    def loop(_client, token, **_kwargs):
        tokens.append(token)
        if len(tokens) < 3:
            raise AgentApiError(401, "invalid_device_token", "The device token is invalid.")

    def enroll_again(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:  # an administrator enrolled the device again meanwhile
            (config_dir / "device-token").write_text("dev_new-value", encoding="utf-8")

    monkeypatch.setattr(meteorcli, "run_loop", loop)
    monkeypatch.setattr(meteorcli.time, "sleep", enroll_again)

    with caplog.at_level("INFO", logger="meteorcli"):
        assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0

    assert tokens == ["dev_secret-value", "dev_secret-value", "dev_new-value"]
    assert sleeps == [meteorcli.SERVICE_RETRY_MIN_SECONDS, meteorcli.SERVICE_RETRY_MIN_SECONDS * 2]
    assert "request-token --new" in caplog.text


def test_service_stays_up_after_an_unexpected_error(tmp_path, run_env, monkeypatch, caplog) -> None:
    _client, sleeps, _ = run_env
    _as_service(monkeypatch)
    config_dir = tmp_path / "meteorcli"
    _write_enrolled(config_dir)
    attempts: list[int] = []

    def loop(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(meteorcli, "run_loop", loop)
    with caplog.at_level("INFO", logger="meteorcli"):
        assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0
    assert len(attempts) == 2
    assert sleeps == [meteorcli.SERVICE_RETRY_MIN_SECONDS]
    assert "Unexpected error" in caplog.text and "boom" in caplog.text


def test_service_waits_instead_of_crashing_on_a_damaged_config(tmp_path, run_env, monkeypatch, caplog) -> None:
    _client, _sleeps, loops = run_env
    _as_service(monkeypatch)
    config_dir = tmp_path / "meteorcli"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"server_url": "http://loc', encoding="utf-8")  # cut off
    (config_dir / "device-token").write_text("dev_secret-value", encoding="utf-8")
    sleeps = _scripted_sleep(monkeypatch, [lambda: None, lambda: _write_enrolled(config_dir)])

    with caplog.at_level("INFO"):
        assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 0

    assert sleeps == [meteorcli.SERVICE_IDLE_CHECK_SECONDS] * 2
    assert "is damaged and was ignored" in caplog.text
    assert loops and loops[0]["token"] == "dev_secret-value"


def test_foreground_run_still_reports_a_refused_credential(tmp_path, run_env, monkeypatch) -> None:
    config_dir = tmp_path / "meteorcli"
    _write_enrolled(config_dir)

    def loop(*_args, **_kwargs):
        raise AgentApiError(401, "invalid_device_token", "The device token is invalid.")

    monkeypatch.setattr(meteorcli, "run_loop", loop)
    assert meteorcli.main(["--config-dir", str(config_dir), "run"]) == 1


def test_unit_always_restarts_the_agent() -> None:
    unit = (Path(__file__).resolve().parents[1] / "packaging" / "meteorcli.service").read_text(encoding="utf-8")
    assert "Restart=always" in unit
    assert "StartLimitIntervalSec=0" in unit
