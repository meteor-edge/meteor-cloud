"""GCP Cloud Run Terraform integration."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from edge_installer.config.models import InstallationConfig
from edge_installer.exceptions import TerraformExecutionError
from edge_installer.process.runner import require_success, run_command
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.state.paths import infrastructure_root, terraform_workdir

logger = logging.getLogger(__name__)


class GcpTerraformRunner:
    def __init__(self, config: InstallationConfig, workdir: Path) -> None:
        self.config = config
        self.workdir = workdir

    def prepare(self) -> None:
        source = infrastructure_root() / "terraform" / "gcp"
        module_source = infrastructure_root() / "terraform" / "modules" / "gcp_cloud_run"
        self.workdir.mkdir(parents=True, exist_ok=True)
        for name in ("main.tf", "variables.tf", "outputs.tf", "versions.tf", ".terraform.lock.hcl"):
            src = source / name
            if src.exists():
                shutil.copy2(src, self.workdir / name)
        dest_module = self.workdir / "modules" / "gcp_cloud_run"
        if dest_module.exists():
            shutil.rmtree(dest_module)
        shutil.copytree(module_source, dest_module)

    def variables(self) -> dict[str, object]:
        cfg = self.config
        gcp = cfg.gcp
        if gcp is None:
            raise TerraformExecutionError("gcp settings are missing", stage="terraform_vars")
        domain = cfg.platform.domain or ""
        public_url = (cfg.platform.public_url or "").rstrip("/")
        return {
            "installation_name": cfg.installation.name,
            "environment": cfg.installation.environment,
            "project_id": gcp.project_id,
            "region": gcp.region,
            "backend_image": cfg.deployment.backend_image,
            "frontend_image": cfg.deployment.frontend_image,
            "postgres_database": cfg.components.postgres.database_name,
            "postgres_username": cfg.components.postgres.username,
            "domain": domain,
            "public_url": public_url,
            "sql_tier": gcp.sql_tier,
            "sql_disk_size_gb": gcp.sql_disk_size_gb,
            "redis_memory_size_gb": gcp.redis_memory_size_gb,
            "deletion_protection": gcp.deletion_protection,
            "min_instances": gcp.min_instances,
            "max_instances": gcp.max_instances,
            "backend_cpu": gcp.backend_cpu,
            "backend_memory": gcp.backend_memory,
            "frontend_cpu": gcp.frontend_cpu,
            "frontend_memory": gcp.frontend_memory,
            "create_artifact_registry": gcp.create_artifact_registry,
            "enable_apis": gcp.enable_apis,
            "subnet_cidr": gcp.subnet_cidr,
            "labels": {
                "installation": cfg.installation.name,
                "environment": cfg.installation.environment,
                "managed-by": "edge-installer",
                "platform": "edge-platform",
            },
        }

    def write_tfvars(self) -> Path:
        path = self.workdir / "terraform.tfvars.json"
        path.write_text(json.dumps(self.variables(), indent=2), encoding="utf-8")
        return path

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if password := os.environ.get("EDGE_PLATFORM_POSTGRES_PASSWORD"):
            env["TF_VAR_postgres_password"] = password
        if secret := os.environ.get("EDGE_PLATFORM_JWT_SECRET"):
            env["TF_VAR_jwt_secret"] = secret
        return env

    def init(self) -> None:
        result = run_command(["terraform", "init", "-input=false"], cwd=str(self.workdir), env=self._env())
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_init")

    def validate(self) -> None:
        result = run_command(["terraform", "validate"], cwd=str(self.workdir), env=self._env())
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_validate")

    def plan(self) -> str:
        self.write_tfvars()
        result = run_command(
            ["terraform", "plan", "-input=false", "-var-file=terraform.tfvars.json"],
            cwd=str(self.workdir),
            env=self._env(),
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
            env=self._env(),
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
            env=self._env(),
        )
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_destroy")

    def read_outputs(self) -> TerraformOutputs:
        result = run_command(["terraform", "output", "-json"], cwd=str(self.workdir), env=self._env())
        require_success(result, error_cls=TerraformExecutionError, stage="terraform_outputs")
        raw = json.loads(result.stdout)
        flattened = {key: value["value"] for key, value in raw.items()}
        return TerraformOutputs.model_validate(flattened)


def gcp_terraform_runner_for(config: InstallationConfig) -> GcpTerraformRunner:
    workdir = terraform_workdir(config.installation.name)
    runner = GcpTerraformRunner(config, workdir)
    runner.prepare()
    return runner
