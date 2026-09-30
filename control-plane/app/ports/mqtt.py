"""MQTT connectivity port.

The control plane publishes commands and consumes device traffic through this
gateway. The current adapter is EMQX via ``data_plane.connectivity.mqtt``.
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
