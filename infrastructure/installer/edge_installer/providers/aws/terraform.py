"""AWS Terraform integration."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from edge_installer.config.models import InstallationConfig
from edge_installer.exceptions import TerraformExecutionError
from edge_installer.process.runner import require_success, run_command
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.state.paths import infrastructure_root, terraform_workdir

logger = logging.getLogger(__name__)


class TerraformRunner:
    def __init__(self, config: InstallationConfig, workdir: Path) -> None:
        self.config = config
        self.workdir = workdir

    def prepare(self) -> None:
        source = infrastructure_root() / "terraform" / "aws"
        modules_source = infrastructure_root() / "terraform" / "modules"
        self.workdir.mkdir(parents=True, exist_ok=True)
        for name in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
            src = source / name
            if src.exists():
                shutil.copy2(src, self.workdir / name)
        if modules_source.exists():
            dest_modules = self.workdir / "modules"
            if dest_modules.exists():
                shutil.rmtree(dest_modules)
            shutil.copytree(modules_source, dest_modules)

    def variables(self) -> dict[str, object]:
        cfg = self.config
        arch = "amd64" if cfg.aws.architecture == "amd64" else "arm64"
        return {
            "installation_name": cfg.installation.name,
            "environment": cfg.installation.environment,
            "enabled_services": cfg.enabled_service_names(),
            "aws_region": cfg.aws.region,
            "aws_profile": cfg.aws.profile or "",
            "availability_zone": cfg.aws.availability_zone or "",
            "instance_type": cfg.aws.instance_type,
            "architecture": arch,
            "ami_id": cfg.aws.ami_id or "",
            "ssh_key_name": cfg.aws.ssh_key_name,
            "root_volume_size_gb": cfg.aws.root_volume_size_gb,
            "assign_elastic_ip": cfg.aws.assign_elastic_ip,
            "allowed_ssh_cidrs": cfg.network.allowed_ssh_cidrs,
            "allow_http": cfg.network.allow_http,
            "allow_https": cfg.network.allow_https,
            "vpn_listen_port": cfg.services.vpn.listen_port,
            "vpn_allowed_client_cidrs": cfg.services.vpn.allowed_client_cidrs,
            "tags": {
                "Installation": cfg.installation.name,
                "Environment": cfg.installation.environment,
                "ManagedBy": "edge-installer",
                "Platform": "edge-platform",
            },
        }

    def write_tfvars(self) -> Path:
        path = self.workdir / "terraform.tfvars.json"
        path.write_text(json.dumps(self.variables(), indent=2), encoding="utf-8")
        return path

    def init(self) -> None:
        result = run_command(["terraform", "init", "-input=false"], cwd=str(self.workdir))
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_init")

    def validate(self) -> None:
        result = run_command(["terraform", "validate"], cwd=str(self.workdir))
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_validate")

    def plan(self) -> str:
        self.write_tfvars()
        result = run_command(
            ["terraform", "plan", "-input=false", "-var-file=terraform.tfvars.json"],
            cwd=str(self.workdir),
        )
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_plan")
        return result.stdout

    def apply(self) -> TerraformOutputs:
        self.write_tfvars()
        result = run_command(
            [
                "terraform",
                "apply",
                "-input=false",
                "-auto-approve",
                "-var-file=terraform.tfvars.json",
            ],
            cwd=str(self.workdir),
        )
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_apply")
        return self.read_outputs()

    def destroy(self) -> None:
        if not (self.workdir / "terraform.tfstate").exists():
            logger.info("No Terraform state found; skipping destroy.")
            return
        self.write_tfvars()
        result = run_command(
            [
                "terraform",
                "destroy",
                "-input=false",
                "-auto-approve",
                "-var-file=terraform.tfvars.json",
            ],
            cwd=str(self.workdir),
        )
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_destroy")

    def read_outputs(self) -> TerraformOutputs:
        result = run_command(["terraform", "output", "-json"], cwd=str(self.workdir))
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_outputs")
        raw = json.loads(result.stdout)
        flattened = {key: value["value"] for key, value in raw.items()}
        return TerraformOutputs.model_validate(flattened)


def terraform_runner_for(config: InstallationConfig) -> TerraformRunner:
    workdir = terraform_workdir(config.installation.name)
    runner = TerraformRunner(config, workdir)
    runner.prepare()
    return runner
