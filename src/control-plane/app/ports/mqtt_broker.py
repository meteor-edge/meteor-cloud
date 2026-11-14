"""MQTT broker port.

MeteorCloud treats the broker as replaceable infrastructure. The control plane
is the source of truth for device identity and topic permissions; every broker
*asks* it (``/internal/mqtt/...``) instead of being provisioned with users and
ACLs. Issuing, rotating, or revoking a credential is therefore one database
write and can never leave the broker out of sync.

The only things that differ per broker are small:

* how the broker reaches the auth endpoints (plugin, HTTP authn/authz, webhooks);
* whether the control plane can drop a live session right away.

Add a broker by implementing this protocol in ``app/adapters/mqtt_brokers.py``
and adding its name to ``MQTT_PROVIDER``. Services depend on this port only.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class MqttBroker(Protocol):
    #: Value of ``MQTT_PROVIDER`` that selects this broker.
    name: str
    #: True when ``disconnect_device`` drops live sessions immediately.
    instant_disconnect: bool
    #: True when the open-source broker can run as a multi-node cluster.
    clustering: bool

    def disconnect_device(self, device_id: uuid.UUID) -> bool:
        """Drop the device's live MQTT sessions. Best effort; never raises.

        Returns True when the request was handed to the broker. Whatever the
        answer, a revoked or disabled device is refused by the auth endpoints
        on its next login, publish, and subscribe.
        """
        ...
