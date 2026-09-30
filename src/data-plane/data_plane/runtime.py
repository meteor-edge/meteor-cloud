"""Process-wide MQTT client used by HTTP handlers."""

from __future__ import annotations

import logging

from data_plane.client import PlatformMqttClient
from data_plane.config import Settings

logger = logging.getLogger(__name__)

_client: PlatformMqttClient | None = None


def get_mqtt_client() -> PlatformMqttClient:
    if _client is None:
        raise RuntimeError("MQTT client is not running.")
    return _client


def set_mqtt_client(client: PlatformMqttClient | None) -> None:
    global _client
    _client = client


def start_mqtt_runtime(settings: Settings) -> None:
    global _client
    if not settings.mqtt_enabled:
        return
    if settings.mqtt_provider != "emqx":
        raise RuntimeError(f"Unsupported mqtt.provider={settings.mqtt_provider!r}; only 'emqx' is implemented")
    client = PlatformMqttClient(settings)
    try:
        client.start()
    except Exception:
        logger.exception("MQTT platform client failed to start")
        _client = None
        return
    _client = client


def stop_mqtt_runtime() -> None:
    global _client
    client = _client
    _client = None
    stop = getattr(client, "stop", None)
    if stop is not None:
        stop()
