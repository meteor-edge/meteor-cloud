"""Docker Compose project apply / health for the device agent.

Uses ``docker compose`` via subprocess. Callers inject ``runner`` in tests.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger("edge_agent.runtime.compose")


class ComposeError(Exception):
    """Raised when a compose operation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def compose_available() -> bool:
    """True when ``docker`` exists and ``docker compose version`` succeeds."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _default_runner(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        capture_output=True,
        text=True,
        timeout=600,
    )


class ComposeAdapter:
    """Bring up a Compose project from a YAML file or zip bundle."""

    def __init__(self, *, runner=_default_runner, work_root: Path | None = None) -> None:
        self._runner = runner
        self.work_root = work_root or Path("/var/lib/meteorcli/compose")

    def apply(
        self,
        *,
        project_name: str,
        artifact: Path,
        env: dict[str, str] | None = None,
    ) -> None:
        name = project_name.strip()
        if not name:
            raise ComposeError("project_name must not be blank")
        if "/" in name or name.startswith("."):
            raise ComposeError(f"Invalid project_name: {name}")
        if not artifact.is_file():
            raise ComposeError(f"Compose artifact not found: {artifact}")

        project_dir = self.work_root / name
        project_dir.mkdir(parents=True, exist_ok=True)
        compose_file = self._materialize_compose_file(artifact, project_dir)

        args = ["docker", "compose", "-p", name, "-f", str(compose_file)]
        if env:
            env_file = project_dir / ".env.meteorcloud"
            env_file.write_text(
                "\n".join(f"{key}={value}" for key, value in env.items()) + "\n",
                encoding="utf-8",
            )
            args.extend(["--env-file", str(env_file)])
        args.extend(["up", "-d", "--remove-orphans"])

        self._run(args)
        logger.info("Started compose project %s from %s", name, compose_file)

    def health_check(self, *, project_name: str) -> bool:
        """True when the project has at least one running service."""
        result = self._runner(
            ["docker", "compose", "-p", project_name.strip(), "ps", "-q", "--status", "running"],
            check=False,
        )
        if result.returncode != 0:
            return False
        return bool((result.stdout or "").strip())

    def recover(self, *, project_name: str) -> None:
        self._run(["docker", "compose", "-p", project_name.strip(), "restart"])

    def _materialize_compose_file(self, artifact: Path, project_dir: Path) -> Path:
        if zipfile.is_zipfile(artifact):
            with zipfile.ZipFile(artifact, "r") as archive:
                archive.extractall(project_dir)
            for candidate in (
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            ):
                path = project_dir / candidate
                if path.is_file():
                    return path
            raise ComposeError(
                "Compose zip must contain docker-compose.yml, docker-compose.yaml, "
                "compose.yml, or compose.yaml."
            )

        dest = project_dir / "compose.yaml"
        dest.write_bytes(artifact.read_bytes())
        return dest

    def _run(self, args: Sequence[str]) -> None:
        try:
            result = self._runner(args, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComposeError(f"docker compose failed ({' '.join(args)}): {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
            raise ComposeError(f"docker compose {' '.join(args[2:])} failed: {detail}")
