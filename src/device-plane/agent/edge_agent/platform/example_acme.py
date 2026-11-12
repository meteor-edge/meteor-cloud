"""Tiny example: host actions for a fictional Acme OS (not used by default).

Tomorrow's NUC on Ubuntu still uses :class:`~edge_agent.platform.linux.LinuxPlatform`.
A *different* OS would look like this: same method names, different internals, then
pass ``host=AcmeHostActions()`` into :class:`~edge_agent.actions.ActionSync`.
"""

from __future__ import annotations

import subprocess

from edge_agent.platform.ports import HostActionError


class AcmeHostActions:
    """Same contract as Linux ``SystemdHostActions``; reboot via a vendor tool."""

    def reboot(self) -> None:
        try:
            subprocess.Popen(["acmectl", "reboot"], start_new_session=True)
        except OSError as exc:
            raise HostActionError(f"acmectl reboot failed ({exc})") from exc

    def shutdown(self) -> None:
        try:
            subprocess.Popen(["acmectl", "poweroff"], start_new_session=True)
        except OSError as exc:
            raise HostActionError(f"acmectl poweroff failed ({exc})") from exc

    def set_password(self, username: str, password: str) -> None:
        raise HostActionError("Acme OS password changes are not implemented in this example.")

    def add_user(
        self,
        username: str,
        password: str,
        *,
        sudo: bool = False,
        authorized_key: str | None = None,
    ) -> None:
        raise HostActionError("Acme OS add-user is not implemented in this example.")
