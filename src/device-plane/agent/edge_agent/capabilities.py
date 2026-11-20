"""Detect which install runtimes this device supports."""

from __future__ import annotations

from typing import Any


def collect_capabilities() -> dict[str, Any]:
    """Capabilities reported on heartbeat (contracts/device-agent-v1.md).

    Booleans say whether a runtime or tool is present on this device; ``actions`` and
    ``deploy_kinds`` list what this agent's code can do.
    """
    from runtime.compose import compose_available
    from runtime.docker import docker_available
    from runtime.systemd import systemd_available

    from edge_agent.actions import SUPPORTED_ACTIONS
    from edge_agent.software import SUPPORTED_KINDS
    from edge_agent.wireguard import sshd_available, wireguard_available

    return {
        "systemd": systemd_available(),
        "docker": docker_available(),
        "docker_compose": compose_available(),
        "wireguard": wireguard_available(),
        "ssh": sshd_available(),
        "actions": list(SUPPORTED_ACTIONS),
        "deploy_kinds": list(SUPPORTED_KINDS),
    }
