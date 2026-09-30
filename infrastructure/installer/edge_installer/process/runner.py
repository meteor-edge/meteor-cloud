"""Safe subprocess execution for Terraform, Ansible, and SSH."""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from edge_installer.exceptions import DependencyMissingError, InstallerError

logger = logging.getLogger(__name__)

SECRET_PATTERNS = (
    re.compile(r"(password|secret|token|key)=\S+", re.IGNORECASE),
    re.compile(r"EDGE_PLATFORM_[A-Z_]+=\S+"),
)


@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def run_command(
    command: Sequence[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stream: bool = True,
) -> ProcessResult:
    """Run a command without shell interpolation."""
    cmd = list(command)
    logger.debug("Running command: %s", " ".join(cmd))

    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DependencyMissingError(
            f"Required command not found: {cmd[0]}",
            stage="dependency_check",
        ) from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    if stream:
        if stdout.strip():
            logger.info(redact(stdout.strip()))
        if stderr.strip() and completed.returncode != 0:
            logger.error(redact(stderr.strip()))

    return ProcessResult(
        command=cmd,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def require_success(result: ProcessResult, *, error_cls: type[InstallerError], stage: str) -> None:
    if result.returncode == 0:
        return
    detail = redact((result.stderr or result.stdout).strip() or "Unknown error")
    raise error_cls(f"Command failed ({result.returncode}): {detail}", stage=stage)


def command_exists(command: str) -> bool:
    result = run_command(["which", command], stream=False)
    return result.returncode == 0
