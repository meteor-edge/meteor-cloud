"""Keep the MQTT session running with the server's current broker settings.

Devices registered before MQTT was enabled, or before the broker CA changed, hold a
missing or stale ``mqtt.json``. Each heartbeat compares the server's config version
with the stored one, refetches credentials when they differ, and (re)starts the session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from edge_agent.client import AgentApiError
from edge_agent.mqtt_config import MqttConfig, mqtt_from_api_payload, read_mqtt_config, write_mqtt_config

logger = logging.getLogger(__name__)


def _default_session_factory(device_id: str, config: MqttConfig, server_url: str | None) -> Any:
    from edge_agent.mqtt import DeviceMqttSession

    return DeviceMqttSession(device_id, config, server_url=server_url)


class MqttSupervisor:
    def __init__(
        self,
        device_id: str,
        config_dir: Path,
        *,
        server_url: str | None = None,
        session_factory: Callable[[str, MqttConfig, str | None], Any] = _default_session_factory,
    ) -> None:
        self.device_id = device_id
        self.config_dir = config_dir
        self.server_url = server_url
        self._factory = session_factory
        self.session: Any = None

    def sync(self, client: Any, device_token: str) -> None:
        status = self._server_status(client, device_token)
        if status is not None and not status.get("enabled"):
            self.stop()
            return
        local = read_mqtt_config(self.config_dir)
        wanted = (status or {}).get("config_version")
        if wanted and (local is None or local.config_version != wanted):
            local = self._refetch(client, device_token) or local
        if self.session is None and local is not None:
            self._start(local)

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()
            self.session = None

    def _server_status(self, client: Any, device_token: str) -> dict[str, Any] | None:
        """None when the server is unreachable or predates this endpoint: keep the local config."""
        try:
            return client.get_mqtt_status(device_token=device_token)
        except AgentApiError as error:
            if error.status != 404:
                logger.warning("Could not check MQTT settings (%s)", error.code)
            return None

    def _refetch(self, client: Any, device_token: str) -> MqttConfig | None:
        try:
            response = client.fetch_mqtt_credentials(device_token=device_token)
        except AgentApiError as error:
            logger.warning("Could not fetch MQTT credentials (%s)", error.code)
            return None
        config = mqtt_from_api_payload({"mqtt": response})
        if config is None:
            return None
        config.config_version = response.get("config_version")
        write_mqtt_config(self.config_dir, config)
        logger.info("MQTT settings changed on the server; reconnecting with new credentials")
        self.stop()
        return read_mqtt_config(self.config_dir)

    def _start(self, config: MqttConfig) -> None:
        session = self._factory(self.device_id, config, self.server_url)
        try:
            session.start()
        except Exception as error:  # retried on the next heartbeat
            logger.error("MQTT failed to start; retrying on the next heartbeat: %s", error)
            return
        self.session = session
