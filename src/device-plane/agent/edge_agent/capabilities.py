"""Detect which install runtimes this device supports."""

from __future__ import annotations

from typing import Any


def collect_capabilities() -> dict[str, Any]:
    """Return capability flags reported on heartbeat (boolean values)."""
    from runtime.compose import compose_available
    from runtime.docker import docker_available
    from runtime.systemd import systemd_available

    return {
        "systemd": systemd_available(),
        "docker": docker_available(),
        "docker_compose": compose_available(),
    }
