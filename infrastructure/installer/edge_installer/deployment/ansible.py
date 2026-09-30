"""Ansible playbook execution."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from edge_installer.deployment.environment import secret_env_vars
from edge_installer.exceptions import AnsibleExecutionError
from edge_installer.process.runner import require_success, run_command
from edge_installer.state.paths import infrastructure_root

logger = logging.getLogger(__name__)


class AnsibleRunner:
    def __init__(self, ansible_root: Path | None = None) -> None:
        self.ansible_root = ansible_root or (infrastructure_root() / "ansible")

    def run_playbook(
        self,
        playbook: str,
        *,
        inventory: Path,
        extra_vars: dict[str, str],
        env: dict[str, str] | None = None,
    ) -> None:
        extra_vars_path = inventory.parent / "extra-vars.json"
        merged = {**extra_vars, **{k: v for k, v in secret_env_vars().items() if v}}
        extra_vars_path.write_text(json.dumps(merged), encoding="utf-8")

        command = [
            "ansible-playbook",
            str(self.ansible_root / "playbooks" / playbook),
            "-i",
            str(inventory),
            "-e",
            f"@{extra_vars_path}",
        ]
        result = run_command(command, cwd=str(self.ansible_root), env=env)
        require_success(result, error_cls=AnsibleExecutionError, stage=f"ansible_{playbook}")
