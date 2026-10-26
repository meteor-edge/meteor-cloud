"""Whether meteorcli.service is already the running agent.

``meteorcli run`` is a foreground loop. The packaged unit runs the same command
in the background. A second copy would duplicate heartbeats, MQTT, and WireGuard.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

UNIT = "meteorcli.service"

Runner = Callable[[], subprocess.CompletedProcess[str]]
PidFn = Callable[[], int]
ParentFn = Callable[[int], int | None]


@dataclass
class ServiceStatus:
    installed: bool
    active: bool
    this_process: bool
    main_pid: int = 0
    load_state: str = "not-found"
    active_state: str = "inactive"

    def line(self) -> str:
        if not self.installed:
            return f"{UNIT} is not installed"
        if self.active:
            if self.this_process:
                who = f"this process, pid {self.main_pid}" if self.main_pid else "this process"
            elif self.main_pid:
                who = f"pid {self.main_pid}"
            else:
                who = "another process"
            return f"{UNIT} is active ({who})"
        if self.active_state == "failed":
            return f"{UNIT} is failed"
        return f"{UNIT} is installed but inactive"


def _systemctl_show() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "show", "-p", "LoadState", "-p", "ActiveState", "-p", "MainPID", "--", UNIT],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def _read_ppid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _is_this_or_ancestor(
    target: int,
    *,
    pid: PidFn,
    read_ppid: ParentFn,
) -> bool:
    if target <= 0:
        return False
    current = pid()
    seen: set[int] = set()
    while current > 0 and current not in seen:
        if current == target:
            return True
        seen.add(current)
        parent = read_ppid(current)
        if parent is None or parent == current:
            break
        current = parent
    return False


def _parse_show(stdout: str) -> tuple[str, str, int]:
    load_state, active_state, main_pid = "not-found", "inactive", 0
    for line in stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "LoadState":
            load_state = value.strip() or load_state
        elif key == "ActiveState":
            active_state = value.strip() or active_state
        elif key == "MainPID":
            try:
                main_pid = int(value.strip())
            except ValueError:
                main_pid = 0
    return load_state, active_state, main_pid


def inspect_service(
    *,
    runner: Runner | None = None,
    pid: PidFn | None = None,
    read_ppid: ParentFn | None = None,
) -> ServiceStatus:
    """Read meteorcli.service without raising. Missing systemd → not installed."""
    import os

    pid_fn = pid or os.getpid
    parent_fn = read_ppid or _read_ppid
    try:
        result = (runner or _systemctl_show)()
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return ServiceStatus(installed=False, active=False, this_process=False)
    load_state, active_state, main_pid = _parse_show(result.stdout)
    installed = load_state == "loaded"
    active = active_state == "active"
    this_process = active and _is_this_or_ancestor(main_pid, pid=pid_fn, read_ppid=parent_fn)
    return ServiceStatus(
        installed=installed,
        active=active,
        this_process=this_process,
        main_pid=main_pid,
        load_state=load_state,
        active_state=active_state,
    )


def duplicate_run_message(status: ServiceStatus) -> str | None:
    """Error text when a second ``meteorcli run`` would clash with the unit."""
    if not status.active or status.this_process:
        return None
    pid = f" (pid {status.main_pid})" if status.main_pid else ""
    return (
        f"error: {UNIT} is already running{pid}.\n"
        "A second 'meteorcli run' would duplicate heartbeats, MQTT, and WireGuard.\n"
        "Follow logs: journalctl -u meteorcli -f\n"
        "To run in the foreground instead: sudo systemctl stop meteorcli"
    )
