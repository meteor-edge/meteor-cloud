"""SSH readiness checks."""

from __future__ import annotations

import logging
import socket
import time
from pathlib import Path

from edge_installer.exceptions import SshConnectionError
from edge_installer.process.runner import run_command

logger = logging.getLogger(__name__)

# Ephemeral EC2 instances often reuse Elastic IPs; ignore global known_hosts conflicts.
_SSH_COMMON_OPTS = (
    "BatchMode=yes",
    "StrictHostKeyChecking=no",
    "UserKnownHostsFile=/dev/null",
    "GlobalKnownHostsFile=/dev/null",
    "ConnectTimeout=5",
)


def wait_for_ssh_port(host: str, *, timeout_seconds: int = 300) -> None:
    logger.info("Waiting for EC2 instance at %s:22...", host)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            try:
                sock.connect((host, 22))
                logger.info("SSH port is reachable.")
                return
            except OSError:
                time.sleep(5)
    raise SshConnectionError(
        f"Timed out waiting for SSH port on {host}",
        stage="ssh_wait",
    )


def verify_ssh_auth(
    host: str,
    username: str,
    private_key_path: Path,
    *,
    timeout_seconds: int = 300,
) -> None:
    logger.info("Verifying SSH authentication...")
    deadline = time.monotonic() + timeout_seconds
    command = ["ssh", "-i", str(private_key_path)]
    for option in _SSH_COMMON_OPTS:
        command.extend(["-o", option])
    command.extend([f"{username}@{host}", "echo ready"])

    while time.monotonic() < deadline:
        result = run_command(command, stream=False)
        if result.returncode == 0:
            logger.info("Server is ready.")
            return
        time.sleep(5)
    raise SshConnectionError(
        f"Unable to authenticate to {username}@{host}",
        stage="ssh_auth",
    )


def wait_for_server(
    host: str,
    username: str,
    private_key_path: Path,
    *,
    timeout_seconds: int = 300,
) -> None:
    wait_for_ssh_port(host, timeout_seconds=timeout_seconds)
    verify_ssh_auth(host, username, private_key_path, timeout_seconds=timeout_seconds)
