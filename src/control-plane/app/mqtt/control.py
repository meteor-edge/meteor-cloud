"""Ask the configured broker to drop a device's live sessions."""

from __future__ import annotations

import uuid

from app.adapters.mqtt_brokers import mqtt_broker
from app.core.config import Settings, get_settings
from app.ports.mqtt import MQTTGateway


def request_device_disconnect(
    publisher: MQTTGateway | None,
    device_id: uuid.UUID,
    settings: Settings | None = None,
) -> bool:
    """Best effort, never raises. See ``MqttBroker.disconnect_device``."""
    settings = settings or get_settings()
    if not settings.mqtt_enabled:
        return False
    return mqtt_broker(settings, publisher).disconnect_device(device_id)
