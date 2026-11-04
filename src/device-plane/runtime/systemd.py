"""systemd unit install / health / recover for the device agent.

Uses ``systemctl`` via subprocess. Unprivileged ``meteorcli run`` goes through
the root-owned ``meteorcli-systemd`` helper (passwordless sudo for that file
only). Callers inject ``runner`` in tests.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger("edge_agent.runtime.systemd")

DEFAULT_UNIT_DIR = Path("/etc/systemd/system")
SYSTEMD_HELPER = "/usr/local/libexec/meteorcli-systemd"
HELPER_HINT = (
    "Cannot install systemd units as this user. Re-run: sudo ./installcli.sh "
    "(installs meteorcli-systemd), or start meteorcli.service."
)


class SystemdError(Exception):
    """Raised when a systemd operation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def systemd_available() -> bool:
    """True when systemctl exists and systemd appears to be the init system."""
    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists() or Path("/run/systemd").exists()


def systemd_helper_installed() -> bool:
    return Path(SYSTEMD_HELPER).is_file()


def _default_runner(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


class SystemdAdapter:
    """Install a unit file and manage it with systemctl."""

    def __init__(
        self,
        *,
        unit_dir: Path | None = None,
        runner=_default_runner,
    ) -> None:
        self.unit_dir = unit_dir or DEFAULT_UNIT_DIR
        self._runner = runner

    def apply(self, *, unit_name: str, unit_file: Path, start: bool = True) -> None:
        """Copy the unit into ``unit_dir``, reload, enable, and optionally start."""
        if not unit_file.is_file():
            raise SystemdError(f"Unit file not found: {unit_file}")
        name = unit_name.strip()
        if not name:
            raise SystemdError("unit_name must not be blank")
        if "/" in name or name.startswith("."):
            raise SystemdError(f"Invalid unit_name: {name}")

        if self._use_helper():
            args = ["apply", name, str(unit_file)]
            if not start:
                args.append("--no-start")
            self._run(self._helper_cmd(*args))
            logger.info("Applied systemd unit %s (start=%s)", name, start)
            return

        self.unit_dir.mkdir(parents=True, exist_ok=True)
        dest = self.unit_dir / name
        try:
            dest.write_bytes(unit_file.read_bytes())
        except OSError as exc:
            raise SystemdError(f"Could not write unit file to {dest}: {exc}") from exc

        self._run(["systemctl", "daemon-reload"])
        self._run(["systemctl", "enable", name])
        if start:
            self._run(["systemctl", "restart", name])
        logger.info("Applied systemd unit %s (start=%s)", name, start)

    def health_check(self, *, unit_name: str) -> bool:
        """Return True when ``systemctl is-active`` reports the unit active."""
        result = self._runner(["systemctl", "is-active", "--quiet", unit_name.strip()], check=False)
        return result.returncode == 0

    def recover(self, *, unit_name: str) -> None:
        """Restart the unit (best-effort recovery)."""
        name = unit_name.strip()
        if self._use_helper():
            self._run(self._helper_cmd("restart", name))
            return
        self._run(["systemctl", "restart", name])

    def _use_helper(self) -> bool:
        return os.geteuid() != 0 and self.unit_dir == DEFAULT_UNIT_DIR

    def _helper_cmd(self, *args: str) -> list[str]:
        if not systemd_helper_installed():
            raise SystemdError(HELPER_HINT)
        return ["sudo", "-n", SYSTEMD_HELPER, *args]

    def _run(self, args: Sequence[str]) -> None:
        try:
            result = self._runner(args, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SystemdError(f"{args[0]} failed ({' '.join(args)}): {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
            raise SystemdError(f"{' '.join(args)} failed: {detail}")
