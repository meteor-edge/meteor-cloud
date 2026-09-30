"""Local MQTT certificate generation and broker TLS config checks."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "generate-local-mqtt-certs.sh"
COMPOSE_CONTROL = ROOT / "compose" / "control-plane.yml"
COMPOSE_DATA = ROOT / "compose" / "data-plane.yml"
EMQX_CONF = ROOT / "infrastructure" / "networking" / "emqx" / "emqx.conf"


def test_certificate_script_generates_localhost_san(tmp_path: Path) -> None:
    out = tmp_path / "certs"
    result = subprocess.run(
        ["bash", str(SCRIPT), str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (out / "ca.crt").is_file()
    assert (out / "server.crt").is_file()
    assert (out / "server.key").is_file()
    assert (out / "server.csr").is_file()
    text = subprocess.check_output(
        ["openssl", "x509", "-in", str(out / "server.crt"), "-noout", "-text"],
        text=True,
    )
    assert "DNS:localhost" in text
    assert "127.0.0.1" in text
    assert "Wrote MQTT" in result.stdout
    assert stat.S_IMODE((out / "server.key").stat().st_mode) == 0o640
    assert stat.S_IMODE((out / "ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((out / "ca.crt").stat().st_mode) == 0o644
    assert stat.S_IMODE((out / "server.crt").stat().st_mode) == 0o644


def test_certificate_script_adds_lan_ip_san(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MQTT_PUBLIC_HOST", "192.168.0.111")
    out = tmp_path / "certs"
    subprocess.run(
        ["bash", str(SCRIPT), str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    text = subprocess.check_output(
        ["openssl", "x509", "-in", str(out / "server.crt"), "-noout", "-text"],
        text=True,
    )
    assert "192.168.0.111" in text
    assert "DNS:localhost" in text


def test_broker_tls_config_exists() -> None:
    control = COMPOSE_CONTROL.read_text(encoding="utf-8")
    data = COMPOSE_DATA.read_text(encoding="utf-8")
    conf = EMQX_CONF.read_text(encoding="utf-8")
    assert "8883:8883" in data
    assert "MQTT_PUBLIC_HOST: ${MQTT_PUBLIC_HOST:-localhost}" in control
    assert "1883:1883" not in data
    assert "listeners.ssl.default" in conf
    assert 'bind = "0.0.0.0:8883"' in conf
    assert "listeners.tcp.default" in conf
    assert "enable = false" in conf
    assert "EMQX_AUTHENTICATION__1__HEADERS" in data
    assert "EMQX_AUTHORIZATION__SOURCES__1__HEADERS" in data
    assert "EMQXVAR_MQTT_INTERNAL_TOKEN: ${MQTT_INTERNAL_TOKEN:-dev-mqtt-internal}" not in data
    assert "${MQTT_INTERNAL_TOKEN:?MQTT_INTERNAL_TOKEN is required}" in data
    assert "getenv(" not in conf
    assert 'x-mqtt-internal-token = "dev-mqtt-internal"' not in conf
