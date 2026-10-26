"""Interactive device shell (browser SSH) port.

The control plane authenticates operators and issues tickets. The provider
opens a PTY to the device over the private network. Destinations are never
taken from the client.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ShellCertificate:
    """Log in with a fresh key and a short-lived certificate instead of the shared gateway key."""

    principal: str
    key_id: str
    # Log sessions: the device runs only this command, and no PTY is allowed.
    force_command: str | None = None


class DeviceShellConnection(Protocol):
    def read(self, n: int = 4096) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...

    @property
    def closed(self) -> bool:
        """True once the remote shell has exited and all its output was read."""
        ...

    @property
    def failure(self) -> str | None:
        """Why the session could not be opened (e.g. SSH refused), or None."""
        ...

    @property
    def received_output(self) -> bool:
        """True once the device sent anything."""
        ...


class DeviceShellProvider(Protocol):
    """Open an interactive session to a private device address."""

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        host_key: str | None = None,
        certificate: ShellCertificate | None = None,
        command: Sequence[str] | None = None,
    ) -> DeviceShellConnection:
        """Connect to ``host:port``. Implementations must refuse non-private hosts.

        ``host_key`` is the device's pinned SSH host key; a different key must be rejected.
        ``certificate``: authenticate with a per-session certificate for that principal.
        ``command``: run this argv without a PTY instead of an interactive shell.
        """
        ...
