"""SSH to environment hosts with a per-environment known_hosts file.

New hosts are trusted on first use (accept-new) and pinned in
.state/<env>/known_hosts; a changed host key fails. Nothing touches the
user's ~/.ssh/known_hosts or global SSH settings.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from e2e.redact import redact
from e2e.state import Host


@dataclass
class SshResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def text(self) -> str:
        return redact((self.stdout + self.stderr).strip())


class Ssh:
    def __init__(self, key_file: Path, known_hosts: Path) -> None:
        self.key_file = key_file
        self.known_hosts = known_hosts

    def options(self) -> list[str]:
        return [
            "-i", str(self.key_file),
            "-o", "IdentitiesOnly=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", "LogLevel=ERROR",
        ]

    def run(self, host: Host, command: str, *, sudo: bool = False, timeout: float = 300) -> SshResult:
        remote = f"sudo -n bash -c {shlex.quote(command)}" if sudo else command
        try:
            completed = subprocess.run(
                ["ssh", *self.options(), f"{host.user}@{host.address}", remote],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return SshResult(124, "", f"timed out after {timeout:.0f}s")
        return SshResult(completed.returncode, completed.stdout, completed.stderr)

    def copy_to(self, host: Host, source: Path, dest: str) -> SshResult:
        return self._scp([str(source), f"{host.user}@{host.address}:{dest}"])

    def copy_from(self, host: Host, source: str, dest: Path) -> SshResult:
        return self._scp([f"{host.user}@{host.address}:{source}", str(dest)])

    def _scp(self, paths: list[str]) -> SshResult:
        completed = subprocess.run(
            ["scp", "-q", "-r", *self.options(), *paths], capture_output=True, text=True, timeout=600, check=False
        )
        return SshResult(completed.returncode, completed.stdout, completed.stderr)
