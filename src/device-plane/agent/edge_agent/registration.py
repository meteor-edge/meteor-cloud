"""Registration workflow: collect inventory, register, and persist credentials."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from edge_agent.client import EdgeClient
from edge_agent.config import AgentConfig, AgentPaths, load_config, save_config
from edge_agent.credentials import read_device_token, remove_file
from edge_agent.inventory import collect_inventory
from edge_agent.mqtt_config import mqtt_from_api_payload
from edge_agent.persist import persist_device_secrets

logger = logging.getLogger("edge_agent")


@dataclass
class RegistrationResult:
    device_id: str
    organization_id: str
    name: str
    heartbeat_interval_seconds: int


def register(
    *,
    client: EdgeClient,
    paths: AgentPaths,
    token: str,
    name: str | None = None,
    token_file: Path | None = None,
) -> RegistrationResult:
    """Register this device and persist its credential and configuration.

    The registration-token file (when provided) is removed only after a
    successful registration so a failed attempt can be retried.
    """
    inventory = collect_inventory()
    # Re-registering hardware the platform already knows needs the current device token
    # (or an administrator's "Allow re-registration"). Only send a stored token to the
    # same server that issued it — not to a different --server target.
    stored = load_config(paths)
    same_server = (
        stored is not None
        and stored.server_url.rstrip("/") == client.server_url.rstrip("/")
    )
    response = client.register(
        token=token,
        inventory=inventory,
        name=name,
        device_token=read_device_token(paths.token_path) if same_server else None,
    )

    persist_device_secrets(
        paths.config_path.parent,
        paths.token_path,
        response["device_token"],
        mqtt_from_api_payload(response),
    )
    existing = load_config(paths)
    config = AgentConfig(
        server_url=client.server_url,
        device_id=response["device_id"],
        organization_id=response["organization_id"],
        name=response["name"],
        heartbeat_interval_seconds=int(response.get("heartbeat_interval_seconds", 60)),
        domain=existing.domain if existing else None,
        api_base=existing.api_base if existing else None,
    )
    save_config(paths, config)

    if token_file is not None:
        remove_file(token_file)

    logger.info("Registered device %s in organization %s", config.device_id, config.organization_id)
    return RegistrationResult(
        device_id=config.device_id,
        organization_id=config.organization_id,
        name=config.name,
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
    )
