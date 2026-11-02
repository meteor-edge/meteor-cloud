"""Ansible playbook execution."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from edge_installer.deployment.environment import secret_env_vars
from edge_installer.exceptions import AnsibleExecutionError
from edge_installer.process.runner import require_success, run_command
from edge_installer.state import paths

logger = logging.getLogger(__name__)


class AnsibleRunner:
    def __init__(self, ansible_root: Path | None = None) -> None:
        self.ansible_root = ansible_root or paths.ansible_root()

    def run_playbook(
        self,
        playbook: str,
        *,
        inventory: Path,
        extra_vars: dict[str, str],
        env: dict[str, str] | None = None,
    ) -> None:
        # Non-secret settings stay on disk so the playbook can be re-run by hand
        # (docs/troubleshooting.md). Secrets go to a separate file that exists only
        # while the playbook runs; the roles read them from the environment otherwise.
        extra_vars_path = inventory.parent / "extra-vars.json"
        _write_private(extra_vars_path, extra_vars)
        secret_vars_path = inventory.parent / "secret-vars.json"
        command = [
            "ansible-playbook",
            str(self.ansible_root / "playbooks" / playbook),
            "-i",
            str(inventory),
            "-e",
            f"@{extra_vars_path}",
            "-e",
            f"@{secret_vars_path}",
        ]
        try:
            _write_private(secret_vars_path, {k: v for k, v in secret_env_vars().items() if v})
            result = run_command(command, cwd=str(self.ansible_root), env=env)
        finally:
            secret_vars_path.unlink(missing_ok=True)
        require_success(result, error_cls=AnsibleExecutionError, stage=f"ansible_{playbook}")


def _write_private(path: Path, values: dict[str, str]) -> None:
    """Write JSON readable only by the current user (0600), also when the file already exists."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(values, handle)
