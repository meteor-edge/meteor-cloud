"""Default Linux (systemd) platform: Raspberry Pi OS, Ubuntu on NUC/Jetson, etc.

Board differences are handled inside the collectors (device-tree vs DMI, thermal
zones). Swap this module only when the host is not Linux/systemd (or when a
board needs a different power/user helper).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from edge_agent import capabilities as capabilities_mod
from edge_agent import inventory as inventory_mod
from edge_agent import metrics as metrics_mod
from edge_agent.platform.ports import HostActionError, Platform

logger = logging.getLogger("edge_agent")

ACTION_HELPER = "/usr/local/libexec/meteorcli-action"
HELPER_TIMEOUT_SECONDS = 60
_NOT_INSTALLED = (
    "The device-action helper is not installed on this device. "
    "Run the meteorcli installer with sudo."
)

Runner = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def helper_installed() -> bool:
    return os.path.isfile(ACTION_HELPER) and os.access(ACTION_HELPER, os.X_OK)


def helper_command(*args: str) -> list[str]:
    base = [ACTION_HELPER] if os.geteuid() == 0 else ["sudo", "-n", ACTION_HELPER]
    return [*base, *args]


def _default_runner(argv: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=HELPER_TIMEOUT_SECONDS,
        check=False,
    )


class LinuxInventory:
    def collect(self) -> dict[str, Any]:
        return inventory_mod.collect_inventory()


class LinuxMetrics:
    def collect(self, *, sample_cpu: bool = True) -> dict[str, Any]:
        return metrics_mod.collect_metrics(sample_cpu=sample_cpu)


class LinuxCapabilities:
    def collect(self) -> dict[str, Any]:
        return capabilities_mod.collect_capabilities()


class SystemdHostActions:
    """Power and users via the root-owned ``meteorcli-action`` helper (systemctl / useradd)."""

    def __init__(
        self,
        *,
        runner: Runner = _default_runner,
        installed: Callable[[], bool] = helper_installed,
    ) -> None:
        self._runner = runner
        self._installed = installed

    def _run(self, command: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._installed():
            raise HostActionError(_NOT_INSTALLED)
        try:
            done = self._runner(helper_command(command), json.dumps(request) if request is not None else None)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostActionError(f"The action helper could not run ({type(exc).__name__}).") from exc
        try:
            answer = json.loads(done.stdout or "{}")
        except ValueError:
            answer = {}
        if not isinstance(answer, dict):
            answer = {}
        if done.returncode != 0:
            message = str(answer.get("error") or "").strip()
            if not message:
                message = (done.stderr or "").strip()[:300] or "The action helper failed."
            raise HostActionError(message)
        return answer

    def reboot(self) -> None:
        self._run("reboot")

    def shutdown(self) -> None:
        self._run("shutdown")

    def set_password(self, username: str, password: str) -> None:
        self._run("set-password", {"username": username, "password": password})

    def add_user(
        self,
        username: str,
        password: str,
        *,
        sudo: bool = False,
        authorized_key: str | None = None,
    ) -> None:
        self._run(
            "add-user",
            {
                "username": username,
                "password": password,
                "sudo": sudo,
                "authorized_key": authorized_key,
            },
        )


class LinuxPlatform:
    """Stock platform for Debian/Ubuntu-class hosts with systemd."""

    def __init__(self, *, host_actions: SystemdHostActions | None = None) -> None:
        self._inventory = LinuxInventory()
        self._metrics = LinuxMetrics()
        self._capabilities = LinuxCapabilities()
        self._host_actions = host_actions or SystemdHostActions()

    @property
    def inventory(self) -> LinuxInventory:
        return self._inventory

    @property
    def metrics(self) -> LinuxMetrics:
        return self._metrics

    @property
    def capabilities(self) -> LinuxCapabilities:
        return self._capabilities

    @property
    def host_actions(self) -> SystemdHostActions:
        return self._host_actions


def default_platform() -> Platform:
    """Platform used by meteorcli on Raspberry Pi, NUC, Jetson (Linux + systemd)."""
    return LinuxPlatform()
