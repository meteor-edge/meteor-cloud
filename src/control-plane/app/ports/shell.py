"""Interactive device shell (browser SSH) port.

The control plane authenticates operators and issues tickets. The provider
opens a PTY to the device over the private network. Destinations are never
taken from the client.
"""

from __future__ import annotations

from typing import Protocol


class DeviceShellConnection(Protocol):
    def read(self, n: int = 4096) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...

    def fileno(self) -> int | None: ...


class DeviceShellProvider(Protocol):
    """Open an interactive session to a private device address."""

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
    ) -> DeviceShellConnection:
        """Connect to ``host:port``. Implementations must refuse non-private hosts."""
        ...
