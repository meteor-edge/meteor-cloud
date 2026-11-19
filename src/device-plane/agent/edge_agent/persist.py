"""Journaled persistence so the device token and MQTT files commit together."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from edge_agent.credentials import write_secret_file
from edge_agent.mqtt_config import MqttConfig, write_mqtt_config
from edge_agent.signing import pin_lock

STAGING_DIRNAME = ".device-secrets-staging"
DONE_NAME = "DONE"


def recover_device_secrets(config_dir: Path) -> None:
    """Finish or discard an interrupted token/MQTT write."""
    staging = config_dir / STAGING_DIRNAME
    if not staging.is_dir():
        return
    done = staging / DONE_NAME
    if not done.is_file():
        shutil.rmtree(staging, ignore_errors=True)
        return
    _commit_and_invalidate_pins(staging, config_dir)
    shutil.rmtree(staging, ignore_errors=True)


def persist_device_secrets(
    config_dir: Path,
    token_path: Path,
    token: str,
    mqtt: MqttConfig | None,
) -> None:
    """Write token and MQTT config via a staging journal, then commit."""
    recover_device_secrets(config_dir)
    staging = config_dir / STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    write_secret_file(staging / token_path.name, token)
    if mqtt is not None:
        write_mqtt_config(staging, mqtt)
        mqtt_path = staging / "mqtt.json"
        data = json.loads(mqtt_path.read_text(encoding="utf-8"))
        if data.get("ca_cert_path"):
            data["ca_cert_path"] = str(config_dir / "mqtt-ca.crt")
            write_secret_file(mqtt_path, json.dumps(data))
    write_secret_file(done_path(staging), json.dumps({"token_file": token_path.name}))
    _commit_and_invalidate_pins(staging, config_dir)
    shutil.rmtree(staging, ignore_errors=True)


def _commit_and_invalidate_pins(staging: Path, config_dir: Path) -> None:
    """Commit the staged credential and drop the previous enrollment's signing pins.

    Both happen under the pin lock so a key fetch that started with the old credential
    cannot save its pins after this (edge_agent.signing.ManifestVerifier._save). The
    pins go before the DONE journal is removed, so a crash retries both.
    """
    with pin_lock(config_dir):
        _commit_staging(staging, config_dir)
        (config_dir / "signing-keys.json").unlink(missing_ok=True)


def done_path(staging: Path) -> Path:
    return staging / DONE_NAME


def _commit_staging(staging: Path, config_dir: Path) -> None:
    done = staging / DONE_NAME
    token_name = "device-token"
    try:
        meta = json.loads(done.read_text(encoding="utf-8"))
        if isinstance(meta, dict) and meta.get("token_file"):
            token_name = str(meta["token_file"])
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    for name in (token_name, "mqtt-ca.crt", "mqtt.json"):
        src = staging / name
        if src.is_file():
            src.replace(config_dir / name)
