"""SSH-over-WireGuard shell provider.

Opens ``ssh`` to a device address that the caller already validated as the
device's WireGuard IP. Private keys stay on the gateway host.
"""

from __future__ import annotations

import ipaddress
import os
import re
import select
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from app.adapters import ssh_certificates
from app.core.exceptions import ValidationAppError
from app.ports.shell import ShellCertificate


def _default_runner(args: Sequence[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )


# ssh exits 255 when it could not connect or authenticate (as opposed to the remote shell's exit code).
_SSH_ERROR_EXIT = 255
# Terminal escape sequences, then any remaining control characters.
_CONTROL_CHARS = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]|[\x00-\x1f\x7f]+")


class SshGatewayConnection:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        known_hosts: str | None = None,
        key_dir: str | None = None,
    ) -> None:
        self._process = process
        self._known_hosts = known_hosts
        self._key_dir = key_dir
        self._tail = b""
        self.received_output = False

    def read(self, n: int = 4096) -> bytes:
        stdout = self._process.stdout
        if stdout is None:
            return b""
        if self._process.poll() is not None:
            return self._remember(stdout.read() or b"")
        ready, _, _ = select.select([stdout], [], [], 0.2)
        if not ready:
            return b""
        return self._remember(os.read(stdout.fileno(), n))

    def _remember(self, chunk: bytes) -> bytes:
        if chunk:
            self._tail = (self._tail + chunk)[-512:]
            self.received_output = True
        return chunk

    @property
    def failure(self) -> str | None:
        """Why ssh could not open the session (last line it printed), or None."""
        if self._process.poll() != _SSH_ERROR_EXIT:
            return None
        lines = [line.strip() for line in self._tail.decode("utf-8", "replace").splitlines() if line.strip()]
        message = _CONTROL_CHARS.sub("", lines[-1]).strip() if lines else ""
        return (message or "ssh could not connect")[:255]

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
        if self._known_hosts:
            try:
                os.unlink(self._known_hosts)
            except FileNotFoundError:
                pass
            self._known_hosts = None
        if self._key_dir:
            shutil.rmtree(self._key_dir, ignore_errors=True)
            self._key_dir = None

    @property
    def closed(self) -> bool:
        # read() drains the remaining output once ssh has exited.
        return self._process.poll() is not None


class SshGatewayProvider:
    """Run OpenSSH against a private device address."""

    def __init__(
        self,
        *,
        identity_file: str = "",
        client_cidr: str = "10.8.0.0/16",
        require_host_key: bool = False,
        ca_file: str = "",
        cert_ttl_seconds: int = 120,
        extra_args: Sequence[str] | None = None,
        runner: Callable[[Sequence[str]], subprocess.Popen[bytes]] = _default_runner,
        keygen_runner: ssh_certificates.Runner | None = None,
    ) -> None:
        self._identity_file = identity_file
        self._client_cidr = client_cidr
        self._require_host_key = require_host_key
        self._ca_file = ca_file
        self._cert_ttl_seconds = cert_ttl_seconds
        self._keygen = keygen_runner or ssh_certificates.run_keygen
        self._extra_args = list(extra_args or [])
        self._runner = runner

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        host_key: str | None = None,
        certificate: ShellCertificate | None = None,
        command: Sequence[str] | None = None,
    ) -> SshGatewayConnection:
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
        if not host_key and self._require_host_key:
            raise ValidationAppError(
                "shell_host_key_unknown",
                "This device has not reported its SSH host key. Update its agent, then retry.",
            )
        args: list[str] = [
            "ssh",
            # A log command gets no PTY: its output is passed through unchanged.
            "-T" if command else "-tt",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=8",
            "-p",
            str(port),
        ]
        known_hosts = None
        if host_key:
            # Pinned at enrollment: trust only that key; a changed key fails instead of being accepted.
            known_hosts = _write_known_hosts(host, port, host_key)
            args.extend(
                [
                    "-o",
                    f"UserKnownHostsFile={known_hosts}",
                    "-o",
                    "GlobalKnownHostsFile=/dev/null",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    f"HostKeyAlgorithms={_host_key_algorithms(host_key)}",
                ]
            )
        else:
            # Older agents do not report a host key: trust on first use (set SHELL_REQUIRE_HOST_KEY to refuse).
            args.extend(["-o", "StrictHostKeyChecking=accept-new"])
        key_dir = None
        try:
            if certificate is not None:
                key_dir = tempfile.mkdtemp(prefix="meteorcloud-ssh-")  # 0700
                key, cert = self._session_key(Path(key_dir), certificate)
                args.extend(["-i", str(key), "-o", f"CertificateFile={cert}"])
            elif self._identity_file:
                args.extend(["-i", self._identity_file])
            args.extend(self._extra_args)
            # "--" ends option parsing: the destination is never read as an ssh option.
            args.extend(["--", f"{username}@{host}"])
            if command:
                # The remote shell parses this one string: quote every argument.
                args.append(shlex.join(command))
            return SshGatewayConnection(self._runner(args), known_hosts=known_hosts, key_dir=key_dir)
        except Exception:
            if known_hosts:
                os.unlink(known_hosts)
            if key_dir:
                shutil.rmtree(key_dir, ignore_errors=True)
            raise

    def _session_key(self, directory: Path, certificate: ShellCertificate) -> tuple[Path, Path]:
        """A key that exists for this session only, and a certificate for one device's principal."""
        key = ssh_certificates.generate_key(directory, runner=self._keygen)
        cert = ssh_certificates.sign_public_key(
            key.with_name(key.name + ".pub"),
            ca_file=self._ca_file,
            key_id=certificate.key_id,
            principal=certificate.principal,
            ttl_seconds=self._cert_ttl_seconds,
            force_command=certificate.force_command,
            runner=self._keygen,
        )
        return key, cert


def _host_key_algorithms(host_key: str) -> str:
    key_type = host_key.split()[0]
    # RSA keys sign with SHA-2; ssh-rsa (SHA-1) stays disabled.
    return "rsa-sha2-512,rsa-sha2-256" if key_type == "ssh-rsa" else key_type


def _write_known_hosts(host: str, port: int, host_key: str) -> str:
    name = host if port == 22 else f"[{host}]:{port}"
    descriptor, path = tempfile.mkstemp(prefix="meteorcloud-known-hosts-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:  # mkstemp creates it 0600
        handle.write(f"{name} {host_key}\n")
    return path


class EchoShellConnection:
    """In-process echo PTY for tests. Never used in production."""

    def __init__(self, *, banner: str = "MeteorCloud shell ready.\r\n", closed: bool = False) -> None:
        self._buffer = bytearray(banner.encode("utf-8"))
        # A log command (closed=True) ends once its output is read.
        self._closed = closed

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
        # Like a real shell, `exit` ends the session once its echo is read.
        self._closed = data.strip() == b"exit"

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed and not self._buffer

    failure: str | None = None
    received_output = True


class EchoShellProvider:
    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        host_key: str | None = None,
        certificate: ShellCertificate | None = None,
        command: Sequence[str] | None = None,
    ) -> EchoShellConnection:
        del host, port, username, host_key, certificate
        if command:
            return EchoShellConnection(banner=f"$ {shlex.join(command)}\r\n", closed=True)
        return EchoShellConnection()
