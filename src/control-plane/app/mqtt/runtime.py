"""Process-wide MQTT gateway used by HTTP handlers."""

from __future__ import annotations

from app.adapters import mqtt_gateway as build_mqtt_gateway
from app.core.config import Settings
from app.mqtt.service import NoopPublisher
from app.ports.mqtt import MQTTGateway

_publisher: MQTTGateway | None = None
_watcher: MQTTGateway | None = None


def get_mqtt_publisher() -> MQTTGateway:
    global _publisher
    if _publisher is None:
        _publisher = NoopPublisher()
    return _publisher


def get_mqtt_topic_watcher() -> MQTTGateway | None:
    return _watcher


def set_mqtt_publisher(publisher: MQTTGateway) -> None:
    global _publisher
    _publisher = publisher


def start_mqtt_runtime(settings: Settings) -> None:
    global _publisher, _watcher
    gateway = build_mqtt_gateway(settings)
    _publisher = gateway
    _watcher = gateway if settings.mqtt_enabled else None


def stop_mqtt_runtime() -> None:
    global _publisher, _watcher
    _publisher = NoopPublisher()
    _watcher = None
