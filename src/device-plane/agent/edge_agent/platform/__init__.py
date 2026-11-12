"""Platform ports for inventory, metrics, capabilities, and host actions.

Device types (Raspberry Pi, NUC, Jetson, …) stay catalog rows in the control plane.
The agent keeps one binary and swaps :class:`~edge_agent.platform.ports.Platform`
adapters when an OS or board needs different collection or power/user helpers.

Default: :func:`default_platform` → Linux + systemd (``meteorcli-action``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from edge_agent.platform.linux import (
    LinuxPlatform,
    SystemdHostActions,
    default_platform,
    helper_command,
    helper_installed,
)
from edge_agent.platform.ports import (
    CapabilityProbe,
    HostActionError,
    HostActions,
    InventoryCollector,
    MetricsCollector,
    Platform,
)

HeartbeatHandler = Callable[[dict[str, Any]], None]


def combine_heartbeat_handlers(*handlers: HeartbeatHandler) -> HeartbeatHandler:
    """Run several post-heartbeat syncs in order (Wi-Fi, actions, …)."""

    def handle(response: dict[str, Any]) -> None:
        for handler in handlers:
            handler(response)

    return handle


__all__ = [
    "CapabilityProbe",
    "HostActionError",
    "HostActions",
    "InventoryCollector",
    "LinuxPlatform",
    "MetricsCollector",
    "Platform",
    "SystemdHostActions",
    "combine_heartbeat_handlers",
    "default_platform",
    "helper_command",
    "helper_installed",
]
