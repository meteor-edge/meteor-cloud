"""SSH-over-WireGuard shell provider.

Opens ``ssh`` to a device address that the caller already validated as the
device's WireGuard IP. Private keys stay on the gateway host.
"""

from __future__ import annotations

import ipaddress
import os
import select
import subprocess
from collections.abc import Callable, Sequence

from app.core.exceptions import ValidationAppError


def _default_runner(args: Sequence[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )


class SshGatewayConnection:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    def read(self, n: int = 4096) -> bytes:
        stdout = self._process.stdout
        if stdout is None:
            return b""
        if self._process.poll() is not None:
            return stdout.read() or b""
        ready, _, _ = select.select([stdout], [], [], 0.2)
        if not ready:
            return b""
        return os.read(stdout.fileno(), n)

    def write(self, data: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None or self._process.poll() is not None:
            return
        stdin.write(data)
        stdin.flush()

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def fileno(self) -> int | None:
        if self._process.stdout is None:
            return None
        return self._process.stdout.fileno()


class SshGatewayProvider:
    """Run OpenSSH against a private device address."""

    def __init__(
        self,
        *,
        identity_file: str = "",
        client_cidr: str = "10.8.0.0/16",
        extra_args: Sequence[str] | None = None,
        runner: Callable[[Sequence[str]], subprocess.Popen[bytes]] = _default_runner,
    ) -> None:
        self._identity_file = identity_file
        self._client_cidr = client_cidr
        self._extra_args = list(extra_args or [])
        self._runner = runner

    def connect(self, *, host: str, port: int, username: str) -> SshGatewayConnection:
        if not host or host.startswith("-"):
            raise ValidationAppError("invalid_shell_host", "Refusing SSH to an invalid host.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValidationAppError("invalid_shell_host", "Refusing SSH to an invalid host.") from exc
        network = ipaddress.ip_network(self._client_cidr, strict=False)
        if address not in network:
            raise ValidationAppError(
                "invalid_shell_host",
                "Refusing SSH outside the device WireGuard network.",
            )
        args: list[str] = [
            "ssh",
            "-tt",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=8",
            "-p",
            str(port),
        ]
        if self._identity_file:
            args.extend(["-i", self._identity_file])
        args.extend(self._extra_args)
        args.append(f"{username}@{host}")
        return SshGatewayConnection(self._runner(args))


class EchoShellConnection:
    """In-process echo PTY for tests. Never used in production."""

    def __init__(self) -> None:
        self._buffer = bytearray(b"MeteorCloud shell ready.\r\n")
        self._closed = False

    def read(self, n: int = 4096) -> bytes:
        if not self._buffer:
            return b""
        chunk = bytes(self._buffer[:n])
        del self._buffer[:n]
        return chunk

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        self._buffer.extend(data)

    def close(self) -> None:
        self._closed = True

    def fileno(self) -> int | None:
        return None


class EchoShellProvider:
    def connect(self, *, host: str, port: int, username: str) -> EchoShellConnection:
        del host, port, username
        return EchoShellConnection()
