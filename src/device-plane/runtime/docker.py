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
from pathlib import Path

logger = logging.getLogger("edge_agent.runtime.docker")

_LOADED_IMAGE_RE = re.compile(r"Loaded image(?: ID)?:?\s*(.+)", re.IGNORECASE)


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
        name = container_name.strip()
        if not name:
            raise DockerError("container_name must not be blank")
        if "/" in name or name.startswith("."):
            raise DockerError(f"Invalid container_name: {name}")

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

    def health_check(self, *, container_name: str) -> bool:
        """True when the named container is running."""
        result = self._runner(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name.strip(),
            ],
            check=False,
        )
        if result.returncode != 0:
            return False
        return (result.stdout or "").strip().lower() == "true"

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
