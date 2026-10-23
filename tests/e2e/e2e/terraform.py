"""Terraform for one environment: tests/e2e/terraform/<provider> with local state in .state/<env>/."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from e2e.config import E2E_DIR, Config
from e2e.proc import run_logged
from e2e.state import State


class Terraform:
    def __init__(self, cfg: Config, state: State) -> None:
        self.cfg = cfg
        self.state = state
        self.root = E2E_DIR / "terraform" / cfg.provider
        self.log = state.logs_dir / "terraform.log"

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(TF_DATA_DIR=str(self.state.tf_data_dir), TF_IN_AUTOMATION="1", TF_INPUT="0")
        if self.cfg.aws_region:
            env["AWS_REGION"] = self.cfg.aws_region
        return env

    def _tf(self, *args: str, check: bool = True, echo: bool = False) -> tuple[int, str]:
        return run_logged(["terraform", f"-chdir={self.root}", *args], log=self.log, env=self._env(),
                          check=check, echo=echo)

    def write_vars(self, ssh_public_key: str, allowed_ssh_cidrs: list[str]) -> None:
        cfg = self.cfg
        if cfg.provider == "aws":
            data: dict[str, Any] = {
                "env_name": cfg.env_name,
                "aws_region": cfg.aws_region,
                "ssh_public_key": ssh_public_key,
                "allowed_ssh_cidrs": allowed_ssh_cidrs,
                "vpn_placement": cfg.vpn_placement,
                "device_count": cfg.device_count,
                "control_plane_instance_type": cfg.control_plane_instance_type,
                "gateway_instance_type": cfg.gateway_instance_type,
                "device_instance_type": cfg.device_instance_type,
                "dns_provider": cfg.dns_provider,
                "dns_zone_id": cfg.dns_zone_id,
                "domain": cfg.domain,
            }
        else:
            data = {
                "env_name": cfg.env_name,
                "aws_region": cfg.aws_region or "us-east-1",
                "dns_provider": cfg.dns_provider,
                "dns_zone_id": cfg.dns_zone_id,
                "domain": cfg.domain,
                "ingress_ip": cfg.onprem_ingress_ip,
            }
        self.state.ensure()
        self.state.tfvars.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(self.state.tfvars, 0o600)

    def init(self) -> None:
        self._tf("init", "-input=false", "-upgrade=false", f"-backend-config=path={self.state.tfstate}",
                 "-reconfigure")

    def apply(self) -> None:
        self._tf("apply", "-input=false", "-auto-approve", f"-var-file={self.state.tfvars}", echo=True)

    def destroy(self) -> None:
        self._tf("destroy", "-input=false", "-auto-approve", f"-var-file={self.state.tfvars}", echo=True)

    def outputs(self) -> dict[str, Any]:
        _, out = self._tf("output", "-json")
        return {k: v.get("value") for k, v in json.loads(out[out.index("{"):]).items()} if "{" in out else {}

    def resources(self) -> list[dict[str, Any]]:
        """Managed resources still in this environment's state (for down's audit and security checks)."""
        if not self.state.tfstate.is_file():
            return []
        data = json.loads(Path(self.state.tfstate).read_text(encoding="utf-8"))
        found = []
        for resource in data.get("resources", []):
            if resource.get("mode") != "managed":
                continue
            for instance in resource.get("instances", []):
                found.append({"type": resource["type"], "name": resource["name"],
                              "module": resource.get("module", ""), "attributes": instance.get("attributes", {})})
        return found
