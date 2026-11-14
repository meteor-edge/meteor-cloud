"""Broker adapters for the ``MqttBroker`` port (Mosquitto, EMQX, VerneMQ)."""

from __future__ import annotations

import json
import logging
import uuid

from app.core.config import Settings
from app.mqtt.acl import mqtt_username_for
from app.mqtt.policy import KICK_TOPIC
from app.ports.mqtt import MQTTGateway
from app.ports.mqtt_broker import MqttBroker

logger = logging.getLogger(__name__)


class MosquittoBroker:
    """Bundled default. The ``meteorcloud_auth`` plugin watches a platform-only
    control topic and disconnects the named user's sessions."""

    name = "mosquitto"
    instant_disconnect = True
    clustering = False

    def __init__(self, publisher: MQTTGateway | None) -> None:
        self._publisher = publisher

    def disconnect_device(self, device_id: uuid.UUID) -> bool:
        if self._publisher is None:
            return False
        payload = json.dumps({"username": mqtt_username_for(device_id)})
        try:
            self._publisher.publish(KICK_TOPIC, payload, qos=1, retain=False)
        except Exception:  # noqa: BLE001 - broker off or unreachable must not fail the API call
            logger.info("Could not ask the MQTT broker to disconnect device %s", device_id)
            return False
        return True


class _ReconnectOnlyBroker:
    """Brokers without a disconnect hook wired up. Access still ends at the
    device's next login, publish, or subscribe (the auth endpoints refuse it)."""

    name = ""
    instant_disconnect = False
    clustering = False

    def disconnect_device(self, device_id: uuid.UUID) -> bool:
        return False


class EmqxBroker(_ReconnectOnlyBroker):
    """EMQX 5.8.6 (Apache 2.0) through its HTTP authn/authz."""

    name = "emqx"
    clustering = True


class VerneMQBroker(_ReconnectOnlyBroker):
    """VerneMQ through ``vmq_webhooks`` (see ``app/mqtt/vernemq_router.py``)."""

    name = "vernemq"
    clustering = True


SUPPORTED_BROKERS = ("mosquitto", "emqx", "vernemq")


def mqtt_broker(settings: Settings, publisher: MQTTGateway | None = None) -> MqttBroker:
    """Return the adapter selected by ``MQTT_PROVIDER``."""
    provider = settings.mqtt_provider
    if provider == "mosquitto":
        return MosquittoBroker(publisher)
    if provider == "emqx":
        return EmqxBroker()
    if provider == "vernemq":
        return VerneMQBroker()
    raise RuntimeError(f"Unsupported mqtt.provider={provider!r}; use one of {', '.join(SUPPORTED_BROKERS)}")
