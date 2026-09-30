"""Issue one-time MQTT passwords for registered devices."""

from __future__ import annotations

import secrets
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.devices.models import Device, DeviceMqttCredential
from app.devices.tokens import hash_token
from data_plane.connectivity.mqtt.acl import mqtt_username_for
from data_plane.connectivity.mqtt.schemas import MqttConnectInfo

MQTT_PASSWORD_PREFIX = "mqtt_"


def generate_mqtt_password() -> str:
    return f"{MQTT_PASSWORD_PREFIX}{secrets.token_urlsafe(32)}"


def _load_ca_cert(settings: Settings) -> str | None:
    path = Path(settings.mqtt_ca_cert_path)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def issue_mqtt_credentials(
    session: Session,
    device: Device,
    settings: Settings,
) -> MqttConnectInfo:
    """Create or rotate the MQTT password. Plaintext is returned only here."""
    plaintext = generate_mqtt_password()
    cred = session.get(DeviceMqttCredential, device.id)
    if cred is None:
        cred = DeviceMqttCredential(
            device_id=device.id,
            password_hash=hash_token(plaintext),
            revoked_at=None,
        )
        session.add(cred)
    else:
        cred.password_hash = hash_token(plaintext)
        cred.revoked_at = None
        session.add(cred)
    session.flush()
    return MqttConnectInfo(
        host=settings.mqtt_public_host,
        port=settings.mqtt_broker_port,
        username=mqtt_username_for(device.id),
        password=plaintext,
        tls=True,
        ca_cert=_load_ca_cert(settings),
    )
