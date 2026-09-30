"""Heartbeat sending with bounded exponential retry.

Sends periodic heartbeats using the stored device credential. Transient
failures are retried with exponential backoff up to a cap; the credential is
never logged. The sleep function is injectable so tests run instantly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from edge_agent.client import AgentApiError, EdgeClient

logger = logging.getLogger("edge_agent")

# Backoff bounds (seconds) for transient heartbeat failures.
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0


def send_heartbeat(
    client: EdgeClient,
    device_token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a single heartbeat and return the server response."""
    return client.heartbeat(device_token=device_token, payload=payload or {})


def _fatal(error: AgentApiError) -> bool:
    """Credential problems are fatal; the loop should stop and re-register."""
    return error.status in {401, 403}


def run_loop(
    client: EdgeClient,
    device_token: str,
    *,
    interval_seconds: int,
    sleep: Callable[[float], None],
    should_continue: Callable[[], bool],
    payload_factory: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """Run the heartbeat loop until ``should_continue`` returns False.

    ``sleep`` and ``should_continue`` are injected so tests can bound iterations
    deterministically without real time delays.
    """
    backoff = _INITIAL_BACKOFF
    while should_continue():
        payload = payload_factory() if payload_factory else {}
        try:
            send_heartbeat(client, device_token, payload)
            backoff = _INITIAL_BACKOFF
            sleep(interval_seconds)
        except AgentApiError as error:
            if _fatal(error):
                logger.error("Heartbeat rejected (%s); credential is invalid.", error.code)
                raise
            logger.warning("Heartbeat failed (%s); retrying in %.0fs.", error.code, backoff)
            sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
