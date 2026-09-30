"""MQTT connect config stored next to the HTTP device token."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from edge_agent.credentials import write_secret_file


@dataclass
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    tls: bool = True
    ca_cert: str | None = None
    ca_path: str | None = None


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def resolve_mqtt_broker_host(stored_host: str, server_url: str | None = None) -> str:
    """If mqtt.json points at loopback, use the HTTP API host instead (LAN devices)."""
    host = (stored_host or "").strip() or "localhost"
    if host.lower() not in _LOOPBACK_HOSTS:
        return host
    if not server_url:
        return host
    api_host = urlparse(server_url).hostname
    if not api_host or api_host.lower() in _LOOPBACK_HOSTS:
        return host
    return api_host


def mqtt_paths(config_dir: Path) -> tuple[Path, Path]:
    return config_dir / "mqtt.json", config_dir / "mqtt-ca.crt"


def write_mqtt_config(config_dir: Path, mqtt: MqttConfig) -> None:
    mqtt_path, ca_path = mqtt_paths(config_dir)
    ca_cert_path = None
    if mqtt.ca_cert:
        write_secret_file(ca_path, mqtt.ca_cert, mode=0o644)
        ca_cert_path = str(ca_path)
    payload = {
        "host": mqtt.host,
        "port": mqtt.port,
        "username": mqtt.username,
        "tls": mqtt.tls,
        "password": mqtt.password,
        "ca_cert_path": ca_cert_path,
    }
    write_secret_file(mqtt_path, json.dumps(payload))


def read_mqtt_config(config_dir: Path) -> MqttConfig | None:
    from edge_agent.persist import recover_device_secrets

    recover_device_secrets(config_dir)
    mqtt_path, _ca_path = mqtt_paths(config_dir)
    try:
        raw = mqtt_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        ca_cert = None
        ca_cert_path = data.get("ca_cert_path")
        if ca_cert_path:
            try:
                ca_cert = Path(ca_cert_path).read_text(encoding="utf-8")
            except OSError:
                ca_cert = None
        password = data.get("password")
        if not password:
            return None
        if data.get("tls", True) is not True:
            return None
        return MqttConfig(
            host=str(data.get("host") or "localhost"),
            port=int(data.get("port") or 8883),
            username=str(data.get("username") or ""),
            password=str(password),
            tls=True,
            ca_cert=ca_cert,
            ca_path=str(ca_cert_path) if ca_cert_path else None,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def mqtt_from_api_payload(payload: dict) -> MqttConfig | None:
    data = payload.get("mqtt")
    if not isinstance(data, dict):
        return None
    password = data.get("password")
    if not password:
        return None
    if data.get("tls", True) is not True:
        return None
    return MqttConfig(
        host=str(data.get("host") or "localhost"),
        port=int(data.get("port") or 8883),
        username=str(data.get("username") or ""),
        password=str(password),
        tls=True,
        ca_cert=data.get("ca_cert"),
    )
