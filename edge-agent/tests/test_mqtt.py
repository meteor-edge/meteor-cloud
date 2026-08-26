"""Reference agent MQTT ping handling and TLS defaults."""

from __future__ import annotations

import json
import os
import stat

from edge_agent.mqtt import (
    handle_command,
    next_backoff,
    normalize_device_topic,
    tls_insecure_enabled,
    verify_broker_tls,
)
from edge_agent.mqtt_config import (
    MqttConfig,
    mqtt_from_api_payload,
    read_mqtt_config,
    resolve_mqtt_broker_host,
    write_mqtt_config,
)
from edge_agent.registration import register
from tests.conftest import FakeClient


def test_handle_ping_returns_pong() -> None:
    result = handle_command(json.dumps({"command_id": "abc", "type": "ping"}))
    assert result == {
        "command_id": "abc",
        "status": "completed",
        "result": {"message": "pong"},
    }


def test_handle_command_requires_command_id() -> None:
    assert handle_command(json.dumps({"type": "ping"})) is None
    assert handle_command("not-json") is None


def test_handle_command_ignores_unknown_type() -> None:
    assert handle_command(json.dumps({"command_id": "abc", "type": "shell"})) is None


def test_collect_metrics_never_raises() -> None:
    from edge_agent.metrics import collect_metrics

    snapshot = collect_metrics()
    assert isinstance(snapshot, dict)
    assert "temperature_c" in snapshot


def test_reconnect_backoff() -> None:
    assert next_backoff(1) == 2
    assert next_backoff(16) == 30
    assert next_backoff(30) == 30


def test_resolve_mqtt_broker_host_uses_api_host_when_loopback() -> None:
    assert resolve_mqtt_broker_host("localhost", "http://192.168.0.111:8000") == "192.168.0.111"
    assert resolve_mqtt_broker_host("127.0.0.1", "http://192.168.0.111:8000") == "192.168.0.111"
    assert resolve_mqtt_broker_host("mqtt.example.com", "http://192.168.0.111:8000") == "mqtt.example.com"
    assert resolve_mqtt_broker_host("localhost", "http://127.0.0.1:8000") == "localhost"


def test_normalize_device_topic() -> None:
    device = "bed66060-1a08-452b-9e17-ffdc29328904"
    assert normalize_device_topic(device, None) == f"devices/{device}/status"
    assert normalize_device_topic(device, "metrics") == f"devices/{device}/metrics"
    assert (
        normalize_device_topic(device, "commands", action="subscribe")
        == f"devices/{device}/commands"
    )
    try:
        normalize_device_topic(device, "events")
    except ValueError as exc:
        assert "must be one of" in str(exc)
    else:
        raise AssertionError("expected events topic to fail")
    try:
        normalize_device_topic(device, "devices/11111111-1111-1111-1111-111111111111/status")
    except ValueError as exc:
        assert "must be under" in str(exc)
    else:
        raise AssertionError("expected foreign topic to fail")
    try:
        normalize_device_topic(device, "devices/+/status")
    except ValueError as exc:
        assert "wildcard" in str(exc).lower()
    else:
        raise AssertionError("expected wildcard to fail")


def test_listen_mqtt_subscribes_to_requested_topic(monkeypatch) -> None:
    from edge_agent.mqtt import listen_mqtt

    subscribed: list[str] = []

    class ReasonCode:
        is_failure = False

    class FakeMqttClient:
        def __init__(self, *args, **kwargs) -> None:
            self.on_connect = None
            self.on_message = None

        def username_pw_set(self, *args, **kwargs) -> None:
            return None

        def tls_set(self, **kwargs) -> None:
            return None

        def tls_insecure_set(self, _value: bool) -> None:
            return None

        def connect_async(self, _host: str, _port: int, _keepalive: int) -> None:
            assert self.on_connect is not None
            self.on_connect(self, None, None, ReasonCode())

        def loop_start(self) -> None:
            return None

        def loop_stop(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

        def subscribe(self, topic: str, qos: int = 0):
            subscribed.append(topic)
            assert qos == 1
            return (0, 1)

    monkeypatch.setattr("edge_agent.mqtt.verify_broker_tls", lambda *args, **kwargs: None)
    monkeypatch.setattr("edge_agent.mqtt.mqtt.Client", FakeMqttClient)
    topic = listen_mqtt(
        "device-1",
        MqttConfig(host="localhost", port=8883, username="u", password="p"),
        tls_insecure=True,
        timeout=0,
        sleep=lambda _seconds: None,
    )
    assert topic == "devices/device-1/commands"
    assert subscribed == ["devices/device-1/commands"]
    subscribed.clear()
    topic = listen_mqtt(
        "device-1",
        MqttConfig(host="localhost", port=8883, username="u", password="p"),
        topic="commands",
        tls_insecure=True,
        timeout=0,
        sleep=lambda _seconds: None,
    )
    assert topic == "devices/device-1/commands"
    assert subscribed == ["devices/device-1/commands"]


def test_verify_broker_tls_reports_unreachable() -> None:
    try:
        verify_broker_tls("127.0.0.1", 1, None, insecure=True, timeout=0.2)
    except RuntimeError as exc:
        assert "not reachable" in str(exc)
        return
    raise AssertionError("expected unreachable broker")


def test_insecure_tls_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("METEORCLI_MQTT_TLS_INSECURE", raising=False)
    assert tls_insecure_enabled() is False
    monkeypatch.setenv("METEORCLI_MQTT_TLS_INSECURE", "1")
    assert tls_insecure_enabled() is True


def test_stop_waits_for_offline_publish_then_disconnects() -> None:
    from edge_agent.mqtt import DeviceMqttSession

    calls: list[str] = []

    class Info:
        def wait_for_publish(self, timeout=None) -> None:
            calls.append(f"wait:{timeout}")

        def is_published(self) -> bool:
            calls.append("is_published")
            return True

    class FakeMqttClient:
        def publish(self, topic, payload, qos=0, retain=False):
            calls.append("publish")
            assert json.loads(payload) == {"status": "offline"}
            return Info()

        def disconnect(self) -> None:
            calls.append("disconnect")

        def loop_stop(self) -> None:
            calls.append("loop_stop")

    session = DeviceMqttSession(
        "device-1",
        MqttConfig(host="localhost", port=8883, username="u", password="p"),
        tls_insecure=True,
    )
    session._client = FakeMqttClient()
    session.stop()
    assert calls == ["publish", "wait:2.0", "is_published", "disconnect", "loop_stop"]
    assert session._client is None


def test_register_stores_mqtt_secret_without_logging(agent_paths, caplog) -> None:
    client = FakeClient(
        register_response={
            "device_id": "11111111-1111-1111-1111-111111111111",
            "device_token": "dev_secret-value",
            "organization_id": "org-1",
            "name": "edge-01",
            "heartbeat_interval_seconds": 60,
            "mqtt": {
                "host": "localhost",
                "port": 8883,
                "username": "device_11111111-1111-1111-1111-111111111111",
                "password": "mqtt_super-secret",
                "tls": True,
                "ca_cert": "-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----\n",
            },
        }
    )
    with caplog.at_level("INFO", logger="edge_agent"):
        register(client=client, paths=agent_paths, token="reg_abc")
    mqtt = read_mqtt_config(agent_paths.config_path.parent)
    assert mqtt is not None
    assert mqtt.password == "mqtt_super-secret"
    mode = stat.S_IMODE((agent_paths.config_path.parent / "mqtt.json").stat().st_mode)
    assert mode == 0o600
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "mqtt_super-secret" not in joined
    assert "dev_secret-value" not in joined


def test_mqtt_from_api_payload_requires_password() -> None:
    assert mqtt_from_api_payload({"mqtt": {"host": "localhost"}}) is None
    cfg = mqtt_from_api_payload(
        {"mqtt": {"host": "localhost", "port": 8883, "username": "u", "password": "p", "tls": True}}
    )
    assert cfg == MqttConfig(host="localhost", port=8883, username="u", password="p", tls=True)
    assert mqtt_from_api_payload(
        {"mqtt": {"host": "localhost", "username": "u", "password": "p", "tls": False}}
    ) is None
    assert (
        mqtt_from_api_payload({"mqtt": {"host": "localhost", "username": "u", "password": "p"}}).tls
        is True
    )


def test_read_mqtt_config_rejects_tls_disabled(tmp_path) -> None:
    (tmp_path / "mqtt.json").write_text(
        json.dumps({"username": "u", "password": "p", "tls": False}),
        encoding="utf-8",
    )
    assert read_mqtt_config(tmp_path) is None


def test_write_mqtt_config_permissions(tmp_path) -> None:
    cfg = MqttConfig(host="localhost", port=8883, username="u", password="p", tls=True, ca_cert="CA")
    write_mqtt_config(tmp_path, cfg)
    assert stat.S_IMODE((tmp_path / "mqtt.json").stat().st_mode) == 0o600
    loaded = read_mqtt_config(tmp_path)
    assert loaded is not None
    assert loaded.ca_cert == "CA"
    assert os.path.exists(tmp_path / "mqtt-ca.crt")


def test_read_mqtt_config_missing_file_returns_none(tmp_path) -> None:
    assert read_mqtt_config(tmp_path) is None


def test_read_mqtt_config_invalid_json_returns_none(tmp_path) -> None:
    (tmp_path / "mqtt.json").write_text("{not-json", encoding="utf-8")
    assert read_mqtt_config(tmp_path) is None


def test_read_mqtt_config_invalid_payload_returns_none(tmp_path) -> None:
    (tmp_path / "mqtt.json").write_text("[]", encoding="utf-8")
    assert read_mqtt_config(tmp_path) is None
    (tmp_path / "mqtt.json").write_text(
        json.dumps({"username": "u", "password": "p", "port": "nope"}),
        encoding="utf-8",
    )
    assert read_mqtt_config(tmp_path) is None


def test_run_continues_if_mqtt_config_read_fails(agent_paths, monkeypatch) -> None:
    import argparse

    from edge_agent import main as agent_main
    from edge_agent.config import AgentConfig, save_config
    from edge_agent.credentials import write_device_token

    save_config(
        agent_paths,
        AgentConfig(
            server_url="http://localhost:8000",
            device_id="device-1",
            organization_id="org-1",
            name="edge-01",
            heartbeat_interval_seconds=60,
        ),
    )
    write_device_token(agent_paths.token_path, "dev_secret")
    ran: list[str] = []
    stopped: list[str] = []

    class BoomSession:
        def start(self) -> None:
            raise AssertionError("should not start")

        def stop(self) -> None:
            stopped.append("stop")

    def boom_read(_dir):
        raise OSError("unreadable mqtt.json")

    monkeypatch.setattr("edge_agent.mqtt_config.read_mqtt_config", boom_read)
    monkeypatch.setattr("edge_agent.mqtt.DeviceMqttSession", lambda *_a, **_k: BoomSession())
    monkeypatch.setattr("edge_agent.main.run_loop", lambda *_a, **_k: ran.append("hb"))
    monkeypatch.setattr("edge_agent.main.EdgeClient", lambda _url: object())
    args = argparse.Namespace(config_dir=str(agent_paths.config_path.parent), once=False)
    assert agent_main._cmd_run(args) == 0
    assert ran == ["hb"]
    assert stopped == []


def test_meteorcli_run_stops_mqtt_if_start_fails(agent_paths, monkeypatch) -> None:
    import argparse

    from edge_agent.config import AgentConfig, save_config
    from edge_agent.credentials import write_device_token
    from meteorcli import cli as meteorcli

    save_config(
        agent_paths,
        AgentConfig(
            server_url="http://localhost:8000",
            device_id="device-1",
            organization_id="org-1",
            name="edge-01",
            heartbeat_interval_seconds=60,
        ),
    )
    write_device_token(agent_paths.token_path, "dev_secret")
    ran: list[str] = []
    stopped: list[str] = []

    class BoomSession:
        def start(self) -> None:
            raise OSError("broker down")

        def stop(self) -> None:
            stopped.append("stop")

    monkeypatch.setattr(
        "edge_agent.mqtt_config.read_mqtt_config",
        lambda _dir: MqttConfig(host="localhost", port=8883, username="u", password="p"),
    )
    monkeypatch.setattr("edge_agent.mqtt.DeviceMqttSession", lambda *_a, **_k: BoomSession())
    monkeypatch.setattr("meteorcli.cli.run_loop", lambda *_a, **_k: ran.append("hb"))
    monkeypatch.setattr("meteorcli.cli.EdgeClient", lambda _url: object())
    args = argparse.Namespace(
        config_dir=str(agent_paths.config_path.parent),
        once=False,
        interval=None,
    )
    assert meteorcli._cmd_run(args) == 0
    assert ran == ["hb"]
    assert stopped == ["stop"]
