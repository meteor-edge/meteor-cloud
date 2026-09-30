"""Journaled device token + MQTT secret persistence."""

from __future__ import annotations

from pathlib import Path

from edge_agent.credentials import read_device_token, write_secret_file
from edge_agent.mqtt_config import MqttConfig, read_mqtt_config
from edge_agent.persist import (
    DONE_NAME,
    STAGING_DIRNAME,
    persist_device_secrets,
    recover_device_secrets,
)


def test_persist_device_secrets_commits_token_and_mqtt(tmp_path: Path) -> None:
    token_path = tmp_path / "device-token"
    mqtt = MqttConfig(
        host="localhost",
        port=8883,
        username="u",
        password="mqtt_secret",
        ca_cert="CA",
    )
    persist_device_secrets(tmp_path, token_path, "dev_secret", mqtt)
    assert read_device_token(token_path) == "dev_secret"
    loaded = read_mqtt_config(tmp_path)
    assert loaded is not None
    assert loaded.password == "mqtt_secret"
    assert loaded.ca_cert == "CA"
    assert loaded.ca_path == str(tmp_path / "mqtt-ca.crt")
    assert not (tmp_path / STAGING_DIRNAME).exists()


def test_recover_discards_incomplete_staging(tmp_path: Path) -> None:
    staging = tmp_path / STAGING_DIRNAME
    staging.mkdir()
    write_secret_file(staging / "device-token", "dev_partial")
    recover_device_secrets(tmp_path)
    assert read_device_token(tmp_path / "device-token") is None
    assert not staging.exists()


def test_recover_commits_complete_staging(tmp_path: Path) -> None:
    staging = tmp_path / STAGING_DIRNAME
    staging.mkdir()
    write_secret_file(staging / "device-token", "dev_recovered")
    write_secret_file(
        staging / "mqtt.json",
        '{"host":"localhost","port":8883,"username":"u","tls":true,"password":"mqtt_p"}',
    )
    write_secret_file(staging / DONE_NAME, '{"token_file":"device-token"}')
    recover_device_secrets(tmp_path)
    assert read_device_token(tmp_path / "device-token") == "dev_recovered"
    loaded = read_mqtt_config(tmp_path)
    assert loaded is not None
    assert loaded.password == "mqtt_p"
    assert not staging.exists()
