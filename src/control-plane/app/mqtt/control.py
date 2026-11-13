"""Platform control messages to the MQTT broker.

The Mosquitto auth plugin watches ``meteorcloud/control/disconnect`` (only the
platform user may publish there) and disconnects every session of the named
user. EMQX ignores the message; a revoked device is then refused at its next
reconnect.
"""

from __future__ import annotations

import json
import logging
import uuid

from app.mqtt.acl import mqtt_username_for
from app.mqtt.policy import KICK_TOPIC
from app.ports.mqtt import MQTTGateway

logger = logging.getLogger(__name__)


def request_device_disconnect(publisher: MQTTGateway | None, device_id: uuid.UUID) -> bool:
    """Ask the broker to drop the device's MQTT sessions. Best effort, never raises."""
    if publisher is None:
        return False
    payload = json.dumps({"username": mqtt_username_for(device_id)})
    try:
        publisher.publish(KICK_TOPIC, payload, qos=1, retain=False)
    except Exception:  # noqa: BLE001 - broker off or unreachable must not fail the API call
        logger.info("Could not ask the MQTT broker to disconnect device %s", device_id)
        return False
    return True
