"""Online status, LWT, reconnect, and command isolation against real EMQX."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from mqtt_live.broker import MqttSession
from mqtt_live.http import wait_mqtt_tls, wait_until


def _device_session(live_config, device, **kwargs) -> MqttSession:
    return MqttSession(
        host=live_config.mqtt_host,
        port=live_config.mqtt_port,
        ca_file=live_config.mqtt_ca_file,
        username=device.mqtt_username,
        password=device.mqtt_password,
        device_id=device.device_id,
        **kwargs,
    )


def _platform_session(live_config) -> MqttSession:
    return MqttSession(
        host=live_config.mqtt_host,
        port=live_config.mqtt_port,
        ca_file=live_config.mqtt_ca_file,
        username=live_config.mqtt_platform_username,
        password=live_config.mqtt_platform_password,
        client_id="live-platform-observer",
    )


def test_lwt_reports_unexpected_disconnect(api, live_config):
    device = api.register_device("lwt")
    status_topic = f"devices/{device.device_id}/status"
    observer = _platform_session(live_config)
    observer.auto_subscribe = [status_topic]
    observer.start()
    session = _device_session(live_config, device)
    try:
        assert observer.wait_connected() is True
        observer.subscribe(status_topic)
        observer.wait_subscribed()
        session.start(lwt_topic=status_topic, lwt_payload=json.dumps({"status": "offline"}))
        assert session.wait_connected() is True
        session.publish(status_topic, json.dumps({"status": "online"}), retain=True)
        session.drop()
        saw_offline = observer.wait_message(
            lambda topic, payload: topic == status_topic and "offline" in payload,
            timeout=20,
        )
        assert saw_offline is not None
        assert wait_until(
            lambda: api.get_device(device.device_id).get("mqtt_status") == "offline",
            timeout_seconds=20,
        )
    finally:
        session.close()
        observer.close()


def test_agent_reconnects_after_broker_restart(api, live_config):
    if not live_config.allow_broker_restart:
        pytest.skip("Broker restart is only enabled for local Compose (MQTT_ALLOW_BROKER_RESTART=1)")
    device = api.register_device("reconnect")
    session = _device_session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
        compose = os.environ.get(
            "MQTT_COMPOSE",
            "docker compose -f deploy/compose/docker-compose.yml",
        )
        result = subprocess.run(
            [*compose.split(), "restart", "emqx"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("Could not restart EMQX (not a local Compose stack)")
        wait_mqtt_tls(live_config.mqtt_host, live_config.mqtt_port, live_config.mqtt_ca_file)
        assert wait_until(lambda: session.connect_count >= 2, timeout_seconds=40)
        assert session.connect_ok is True
    finally:
        session.close()


def test_other_device_cannot_complete_foreign_command(api, live_config):
    device_a = api.register_device("cmd-a")
    device_b = api.register_device("cmd-b")
    session_a = _device_session(
        live_config,
        device_a,
        auto_subscribe=[f"devices/{device_a.device_id}/commands"],
        handle_ping=True,
    )
    session_b = _device_session(live_config, device_b)
    session_a.start()
    session_b.start()
    try:
        assert session_a.wait_connected() is True
        assert session_a.wait_subscribed() is True
        assert session_b.wait_connected() is True
        hijack = json.dumps(
            {
                "command_id": "00000000-0000-0000-0000-000000000001",
                "status": "failed",
                "result": {"message": "hijack"},
            }
        )
        published = session_b.publish(f"devices/{device_a.device_id}/commands/result", hijack)
        assert (not published) or session_b.disconnect_event.wait(3)
        response = api.ping(device_a.device_id)
        assert response.get("status") == "completed"
        result = response.get("result") or {}
        assert result.get("message") == "pong" or (result.get("result") or {}).get("message") == "pong"
    finally:
        session_a.close()
        session_b.close()


def test_device_metrics_reach_platform(api, live_config):
    device = api.register_device("metrics-pub")
    topic = f"devices/{device.device_id}/metrics"
    observer = _platform_session(live_config)
    observer.auto_subscribe = [topic]
    session = _device_session(live_config, device)
    observer.start()
    session.start()
    try:
        assert observer.wait_connected() is True
        assert observer.wait_subscribed() is True
        assert session.wait_connected() is True
        payload = json.dumps(
            {
                "timestamp": "2026-08-25T12:00:00Z",
                "cpu_percent": 18.4,
                "memory_percent": 42.1,
                "disk_percent": 61.3,
                "temperature_c": 54.2,
            }
        )
        assert session.publish(topic, payload) is True
        found = observer.wait_message(
            lambda t, body: t == topic and "18.4" in body,
            timeout=15,
        )
        assert found is not None
        assert wait_until(
            lambda: (api.get_device(device.device_id).get("mqtt_metrics") or {}).get("cpu_percent")
            == 18.4,
            timeout_seconds=20,
        )
    finally:
        session.close()
        observer.close()
