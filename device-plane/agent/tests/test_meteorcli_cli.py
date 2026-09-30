"""Tests for the `meteorcli` command-line interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from edge_agent.client import AgentApiError
from meteorcli import cli as meteorcli
from meteorcli.config import default_config_dir, derive_api_base
from tests.conftest import FakeClient


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
    assert default_config_dir() == home / ".config" / "meteorcli"


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
