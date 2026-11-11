"""Whether meteorcli.service is already the running agent.

``meteorcli run`` is a foreground loop. The packaged unit runs the same command
in the background. A second copy would duplicate heartbeats, MQTT, and WireGuard.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

UNIT = "meteorcli.service"
NOT_INSTALLED = "not installed"
_PROC_CGROUP = Path("/proc/self/cgroup")


def running_as_service(cgroup_file: Path = _PROC_CGROUP) -> bool:
    """True when this process runs inside meteorcli.service.

    systemd puts every unit in its own cgroup (``.../system.slice/meteorcli.service``).
    ``INVOCATION_ID`` is not enough: desktop terminals started by ``systemd --user``
    pass it on to every interactive shell.
    """
    try:
        lines = cgroup_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        path = line.split(":", 2)[-1]
        if path.rstrip("/").rsplit("/", 1)[-1] == UNIT:
            return True
    return False


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
    if state != "active" or running_as_service():
        return None
    return (
        f"error: {UNIT} is already running.\n"
        "A second 'meteorcli run' would duplicate heartbeats, MQTT, and WireGuard.\n"
        "Follow logs: journalctl -u meteorcli -f\n"
        "To run in the foreground instead: sudo systemctl stop meteorcli"
    )
