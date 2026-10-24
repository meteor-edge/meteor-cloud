"""MqttSupervisor refetches stale MQTT settings and keeps the session running."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from edge_agent.client import AgentApiError
from edge_agent.mqtt_config import MqttConfig, read_mqtt_config, write_mqtt_config
from edge_agent.mqtt_sync import MqttSupervisor

CA = "-----BEGIN CERTIFICATE-----\nnew\n-----END CERTIFICATE-----\n"


class Server:
    def __init__(self, status: dict[str, Any] | AgentApiError) -> None:
        self.status = status
        self.fetches = 0

    def get_mqtt_status(self, *, device_token: str) -> dict[str, Any]:
        if isinstance(self.status, AgentApiError):
            raise self.status
        return self.status

    def fetch_mqtt_credentials(self, *, device_token: str) -> dict[str, Any]:
        self.fetches += 1
        return {
            "host": "demo.example.com",
            "port": 8883,
            "username": "device_1",
            "password": f"mqtt_new{self.fetches}",
            "tls": True,
            "ca_cert": CA,
            "config_version": "v2",
        }


class Session:
    def __init__(self, config: MqttConfig, fail: bool) -> None:
        self.config = config
        self.fail = fail
        self.stopped = False

    def start(self) -> None:
        if self.fail:
            raise RuntimeError("certificate verify failed")

    def stop(self) -> None:
        self.stopped = True


def _supervisor(tmp_path: Path, failures: list[bool] | None = None) -> tuple[MqttSupervisor, list[Session]]:
    started: list[Session] = []
    plan = list(failures or [])

    def factory(device_id: str, config: MqttConfig, server_url: str | None) -> Session:
        session = Session(config, plan.pop(0) if plan else False)
        started.append(session)
        return session

    return MqttSupervisor("1", tmp_path, session_factory=factory), started


def _stale_config(tmp_path: Path) -> None:
    write_mqtt_config(
        tmp_path,
        MqttConfig(host="demo.example.com", port=8883, username="device_1", password="mqtt_old", ca_cert="old"),
    )


def test_refetches_config_registered_before_versions(tmp_path: Path) -> None:
    _stale_config(tmp_path)
    server = Server({"enabled": True, "config_version": "v2"})
    supervisor, started = _supervisor(tmp_path)

    supervisor.sync(server, "tok")

    assert server.fetches == 1
    stored = read_mqtt_config(tmp_path)
    assert stored is not None
    assert stored.config_version == "v2"
    assert stored.ca_cert == CA
    assert started[-1].config.password == "mqtt_new1"
    # Same version on the next beat: no refetch, no restart.
    supervisor.sync(server, "tok")
    assert server.fetches == 1
    assert len(started) == 1


def test_fetches_config_when_device_has_none(tmp_path: Path) -> None:
    server = Server({"enabled": True, "config_version": "v2"})
    supervisor, started = _supervisor(tmp_path)

    supervisor.sync(server, "tok")

    assert server.fetches == 1
    assert supervisor.session is started[0]


def test_retries_start_on_next_heartbeat(tmp_path: Path) -> None:
    server = Server({"enabled": True, "config_version": "v2"})
    supervisor, started = _supervisor(tmp_path, failures=[True])

    supervisor.sync(server, "tok")
    assert supervisor.session is None

    supervisor.sync(server, "tok")
    assert supervisor.session is started[1]


def test_restarts_when_server_version_changes(tmp_path: Path) -> None:
    server = Server({"enabled": True, "config_version": "v2"})
    supervisor, started = _supervisor(tmp_path)
    supervisor.sync(server, "tok")

    server.status = {"enabled": True, "config_version": "v3"}
    supervisor.sync(server, "tok")

    assert started[0].stopped
    assert supervisor.session is started[1]


def test_stops_when_mqtt_is_disabled(tmp_path: Path) -> None:
    server = Server({"enabled": True, "config_version": "v2"})
    supervisor, started = _supervisor(tmp_path)
    supervisor.sync(server, "tok")

    server.status = {"enabled": False, "config_version": None}
    supervisor.sync(server, "tok")

    assert started[0].stopped
    assert supervisor.session is None


def test_older_server_keeps_local_config(tmp_path: Path) -> None:
    _stale_config(tmp_path)
    server = Server(AgentApiError(404, "not_found", "Not Found"))
    supervisor, started = _supervisor(tmp_path)

    supervisor.sync(server, "tok")

    assert server.fetches == 0
    assert started[0].config.password == "mqtt_old"
