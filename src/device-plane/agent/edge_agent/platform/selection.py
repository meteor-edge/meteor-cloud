"""Choose the platform adapters for this agent process.

``METEORCLI_PLATFORM`` picks them: ``linux`` (the default) or ``simulated``. The
simulator is never chosen automatically. Agent code collects inventory, metrics and
capabilities through the helpers here, so the choice applies everywhere.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from edge_agent.platform.ports import Platform

logger = logging.getLogger("edge_agent")

ENV_PLATFORM = "METEORCLI_PLATFORM"
PLATFORMS = ("linux", "simulated")

_lock = threading.Lock()
_active: Platform | None = None


def platform_name() -> str:
    """The configured platform name; unknown values fall back to ``linux`` with a warning."""
    name = os.environ.get(ENV_PLATFORM, "").strip().lower() or "linux"
    if name not in PLATFORMS:
        logger.warning("Unknown %s=%r; using linux (choices: %s).", ENV_PLATFORM, name, ", ".join(PLATFORMS))
        return "linux"
    return name


def active_platform() -> Platform:
    """The platform for this process, created on first use."""
    global _active
    with _lock:
        if _active is None:
            if platform_name() == "simulated":
                from edge_agent.platform.simulated import SimulatedPlatform

                simulated = SimulatedPlatform()
                logger.warning(
                    "Simulated device: profile %s, serial %s. Hardware facts are not real.",
                    simulated.profile["name"],
                    simulated.inventory.serial,
                )
                _active = simulated
            else:
                from edge_agent.platform.linux import LinuxPlatform

                _active = LinuxPlatform()
        return _active


def reset_active_platform() -> None:
    """Forget the chosen platform (tests, or after changing the environment)."""
    global _active
    with _lock:
        _active = None


def collect_inventory() -> dict[str, Any]:
    return active_platform().inventory.collect()


def collect_metrics(*, sample_cpu: bool = True) -> dict[str, Any]:
    return active_platform().metrics.collect(sample_cpu=sample_cpu)


def collect_capabilities() -> dict[str, Any]:
    return active_platform().capabilities.collect()
