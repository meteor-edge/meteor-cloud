"""MQTT connectivity port.

The control plane publishes and watches topics through this gateway.
The current adapter is the data-plane HTTP API (EMQX behind that service).
"""

from __future__ import annotations

from typing import Protocol


class MQTTGateway(Protocol):
    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None: ...

    def watch_topic(self, topic: str) -> None: ...

    def unwatch_topic(self, topic: str) -> None: ...
