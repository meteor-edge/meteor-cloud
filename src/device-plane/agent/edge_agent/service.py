"""Whether meteorcli.service is already the running agent.

``meteorcli run`` is a foreground loop. The packaged unit runs the same command
in the background. A second copy would duplicate heartbeats, MQTT, and WireGuard.
"""

from __future__ import annotations

import os
import subprocess

UNIT = "meteorcli.service"
NOT_INSTALLED = "not installed"


def running_under_systemd() -> bool:
    """systemd sets INVOCATION_ID for every process it starts for a unit."""
    return bool(os.environ.get("INVOCATION_ID"))


def service_state() -> str:
    """``not installed``, or the unit's ActiveState (active, inactive, failed, ...). Never raises."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "-p", "LoadState", "-p", "ActiveState", "--", UNIT],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return NOT_INSTALLED
    props = dict(line.partition("=")[::2] for line in result.stdout.splitlines())
    if props.get("LoadState") != "loaded":
        return NOT_INSTALLED
    return props.get("ActiveState") or "inactive"


def duplicate_run_message(state: str) -> str | None:
    """Error text when a foreground ``meteorcli run`` would clash with the active unit."""
    if state != "active" or running_under_systemd():
        return None
    return (
        f"error: {UNIT} is already running.\n"
        "A second 'meteorcli run' would duplicate heartbeats, MQTT, and WireGuard.\n"
        "Follow logs: journalctl -u meteorcli -f\n"
        "To run in the foreground instead: sudo systemctl stop meteorcli"
    )
