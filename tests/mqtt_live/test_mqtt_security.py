"""MQTT authn/authz/TLS against a real EMQX broker."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mqtt_live.broker import MqttSession
from mqtt_live.http import wait_until


def _session(live_config, device, **kwargs) -> MqttSession:
    params = {
        "host": live_config.mqtt_host,
        "port": live_config.mqtt_port,
        "ca_file": live_config.mqtt_ca_file,
        "username": device.mqtt_username,
        "password": device.mqtt_password,
        "device_id": device.device_id,
    }
    params.update(kwargs)
    return MqttSession(**params)


def test_valid_device_connects(api, live_config):
    device = api.register_device("sec-ok")
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
    finally:
        session.close()


def test_wrong_password_denied(api, live_config):
    device = api.register_device("sec-bad-pw")
    session = _session(live_config, device, password="wrong-password")
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_anonymous_denied(live_config):
    session = MqttSession(
        host=live_config.mqtt_host,
        port=live_config.mqtt_port,
        ca_file=live_config.mqtt_ca_file,
        username=None,
        password=None,
    )
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_disabled_device_denied(api, live_config):
    device = api.register_device("sec-disabled")
    api.disable_device(device.device_id)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_revoked_credential_denied(api, live_config):
    device = api.register_device("sec-revoked")
    api.revoke_mqtt(device.device_id)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_own_topics_allowed(api, live_config):
    device = api.register_device("sec-own")
    commands = f"devices/{device.device_id}/commands"
    session = _session(live_config, device, auto_subscribe=[commands])
    session.start()
    try:
        assert session.wait_connected() is True
        assert session.wait_subscribed() is True
        assert session.publish(
            f"devices/{device.device_id}/status",
            json.dumps({"status": "online"}),
            retain=True,
        )
        assert session.publish(
            f"devices/{device.device_id}/metrics",
            json.dumps({"cpu_percent": 10.0, "memory_percent": 20.0, "disk_percent": 30.0}),
        )
        session.subscribe(f"devices/{device.device_id}/events")
        subscribed = session.wait_subscribed()
        # deny_action=disconnect can drop the socket before SUBACK; paho may then
        # reconnect and succeed on the original commands subscribe.
        denied_sub = (not subscribed) or session.disconnect_event.is_set()
        assert denied_sub
        published = session.publish(
            f"devices/{device.device_id}/events",
            json.dumps({"event": "boot"}),
        )
        denied = (not published) or session.disconnect_event.wait(8)
        assert denied
    finally:
        session.close()


def test_other_device_topics_denied(api, live_config):
    device_a = api.register_device("sec-a")
    device_b = api.register_device("sec-b")
    session = _session(live_config, device_a)
    session.start()
    try:
        assert session.wait_connected() is True
        session.subscribe(f"devices/{device_b.device_id}/commands")
        assert session.wait_subscribed() is False
        published = session.publish(
            f"devices/{device_b.device_id}/status",
            json.dumps({"status": "online"}),
        )
        denied = (not published) or session.disconnect_event.wait(8)
        assert denied
    finally:
        session.close()


def test_valid_tls_connects(api, live_config):
    device = api.register_device("sec-tls")
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
    finally:
        session.close()


def test_untrusted_ca_denied(api, live_config, tmp_path: Path):
    key = tmp_path / "wrong.key"
    cert = tmp_path / "wrong.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=untrusted-mqtt-ca",
        ],
        check=True,
        capture_output=True,
    )
    device = api.register_device("sec-bad-ca")
    session = _session(live_config, device, untrusted_ca=str(cert))
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_device_reports_online(api, live_config):
    device = api.register_device("sec-online")
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
        session.publish(
            f"devices/{device.device_id}/status",
            json.dumps({"status": "online"}),
            retain=True,
        )
        assert wait_until(
            lambda: api.get_device(device.device_id).get("mqtt_status") == "online",
            timeout_seconds=20,
        )
    finally:
        session.close()
