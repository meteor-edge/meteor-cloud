"""Single-container docker run / load / health for the device agent.

Uses the ``docker`` CLI via subprocess. Callers inject ``runner`` in tests.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("edge_agent.runtime.docker")

_LOADED_IMAGE_RE = re.compile(r"Loaded image(?: ID)?:?\s*(.+)", re.IGNORECASE)

# Only these state fields are read: never Config.Env, labels or logs (they can hold secrets).
INSPECT_HEALTH_TEMPLATE = (
    "{{.State.Running}}|{{.State.Status}}|{{.State.ExitCode}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"
)

HEALTHY = "healthy"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ContainerHealth:
    """``status`` is healthy, unhealthy or unknown; ``problem`` says why it is not healthy."""

    status: str
    problem: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == HEALTHY

    def reason(self, subject: str) -> str | None:
        """e.g. ``container web is not running (exited, exit 1)``."""
        return f"{subject} {self.problem}" if self.problem else None


def inspect_container_health(runner, container_name: str) -> ContainerHealth:
    """Running state plus Docker HEALTHCHECK (when the image defines one), from one ``docker inspect``."""
    try:
        result = runner(["docker", "inspect", "-f", INSPECT_HEALTH_TEMPLATE, container_name], check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ContainerHealth(UNHEALTHY, "could not be inspected")
    if result.returncode != 0:
        return ContainerHealth(UNHEALTHY, "not found")
    running, status, exit_code, health = ((result.stdout or "").strip().split("|") + ["", "", "", ""])[:4]
    if running.lower() != "true":
        detail = ", ".join(part for part in (status, f"exit {exit_code}" if exit_code else "") if part)
        return ContainerHealth(UNHEALTHY, "is not running" + (f" ({detail})" if detail else ""))
    if health == "unhealthy":
        return ContainerHealth(UNHEALTHY, "HEALTHCHECK failed")
    if health == "starting":
        # Coming up: neither healthy nor failed yet, so the URL does not flap.
        return ContainerHealth(UNKNOWN, "HEALTHCHECK starting")
    return ContainerHealth(HEALTHY)


def validate_container_name(container_name: str) -> str:
    name = container_name.strip()
    if not name:
        raise DockerError("container_name must not be blank")
    if "/" in name or name.startswith("."):
        raise DockerError(f"Invalid container_name: {name}")
    return name


class DockerError(Exception):
    """Raised when a docker operation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def docker_available() -> bool:
    """True when the ``docker`` CLI is on PATH."""
    return shutil.which("docker") is not None


def _default_runner(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        capture_output=True,
        text=True,
        timeout=600,
    )


class DockerAdapter:
    """Install and run one container (pull image or load a tarball)."""

    def __init__(self, *, runner=_default_runner) -> None:
        self._runner = runner

    def apply(
        self,
        *,
        container_name: str,
        image: str | None = None,
        tarball: Path | None = None,
        ports: list[str] | None = None,
        env: dict[str, str] | None = None,
        restart: str | None = "unless-stopped",
        command: str | None = None,
    ) -> None:
        name = validate_container_name(container_name)

        resolved_image = (image or "").strip() or None
        if tarball is not None:
            loaded = self.load_tarball(tarball)
            if not resolved_image:
                resolved_image = loaded
        if not resolved_image:
            raise DockerError("A docker release needs an image name or a loadable tarball.")

        self._run(["docker", "rm", "-f", name], check=False)

        args = ["docker", "run", "-d", "--name", name]
        if restart:
            args.extend(["--restart", restart])
        for port in ports or []:
            args.extend(["-p", port])
        for key, value in (env or {}).items():
            args.extend(["-e", f"{key}={value}"])
        args.append(resolved_image)
        if command:
            args.extend(shlex.split(command))

        self._run(args)
        logger.info("Started container %s from %s", name, resolved_image)

    def load_tarball(self, tarball: Path) -> str:
        """``docker load`` a save tarball; return the loaded image reference."""
        if not tarball.is_file():
            raise DockerError(f"Docker tarball not found: {tarball}")
        result = self._run(["docker", "load", "-i", str(tarball)])
        combined = f"{result.stdout or ''}\n{result.stderr or ''}"
        for line in combined.splitlines():
            match = _LOADED_IMAGE_RE.search(line.strip())
            if match:
                return match.group(1).strip()
        raise DockerError("docker load succeeded but no image name was reported.")

    def inspect_health(self, *, container_name: str) -> ContainerHealth:
        return inspect_container_health(self._runner, validate_container_name(container_name))

    def health_check(self, *, container_name: str) -> bool:
        """True only when the container is running and its HEALTHCHECK (if any) passes."""
        return self.inspect_health(container_name=container_name).healthy

    def recover(self, *, container_name: str) -> None:
        self._run(["docker", "restart", container_name.strip()])

    def _run(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = self._runner(args, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerError(f"docker failed ({' '.join(args)}): {exc}") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
            raise DockerError(f"docker {' '.join(args[1:])} failed: {detail}")
        return result
