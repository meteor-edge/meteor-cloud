"""Platform deployment orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from edge_installer.config.models import InstallationConfig
from edge_installer.config.validation import ensure_valid
from edge_installer.deployment.ansible import AnsibleRunner
from edge_installer.deployment.environment import build_ansible_extra_vars, platform_url
from edge_installer.deployment.inventory import write_inventory
from edge_installer.deployment.ssh import wait_for_server
from edge_installer.exceptions import InstallerError
from edge_installer.health.service import HealthService
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.providers.aws.provider import AwsEc2Provider
from edge_installer.state.models import InstallationState
from edge_installer.state.paths import installation_dir, installation_state_file
from edge_installer.state.store import InstallationLock, load_state, save_state

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplyResult:
    state: InstallationState
    outputs: TerraformOutputs
    health: dict[str, str]


class PlatformDeploymentService:
    def __init__(self, config: InstallationConfig) -> None:
        self.config = config
        self.provider = AwsEc2Provider(config)
        self.ansible = AnsibleRunner()
        self.health = HealthService()

    def validate(self) -> None:
        ensure_valid(self.config)

    def plan(self) -> dict[str, object]:
        self.validate()
        result = self.provider.plan()
        result["enabled_services"] = self.config.enabled_service_names()
        return result

    def apply(self) -> ApplyResult:
        self.validate()
        enabled = self.config.enabled_service_names()
        name = self.config.installation.name
        total_stages = 5 + len(enabled)

        with InstallationLock(name):
            stage = 1
            logger.info("[%s/%s] Validating configuration", stage, total_stages)
            stage += 1

            logger.info("[%s/%s] Applying Terraform (services: %s)", stage, total_stages, enabled)
            outputs = self._apply_infrastructure()
            stage += 1

            logger.info("[%s/%s] Waiting for SSH", stage, total_stages)
            self._wait_for_ssh(outputs)
            stage += 1

            logger.info("[%s/%s] Running Ansible (provision + deploy)", stage, total_stages)
            self._run_site(outputs)
            stage += 1

            health: dict[str, str] = {"status": "skipped"}
            if "cloud_app" in enabled:
                logger.info("[%s/%s] Verifying cloud app health", stage, total_stages)
                url = platform_url(self.config, outputs)
                report = self.health.verify(
                    url,
                    timeout_seconds=self.config.deployment.health_check_timeout_seconds,
                )
                health = report.as_dict()
            else:
                logger.info(
                    "[%s/%s] Skipping cloud app health (cloud_app disabled)",
                    stage,
                    total_stages,
                )

            url = platform_url(self.config, outputs) if "cloud_app" in enabled else None
            state = self._save_state(outputs, url, health, enabled)
            return ApplyResult(state=state, outputs=outputs, health=health)

    def status(self) -> dict[str, object]:
        state = load_state(self.config.installation.name)
        result: dict[str, object] = {"state": state.model_dump() if state else None}
        try:
            outputs = self.provider.terraform.read_outputs()
            result["infrastructure"] = outputs.model_dump()
            if state and state.platform_url and "cloud_app" in self.config.enabled_service_names():
                result["health"] = self.health.verify(
                    state.platform_url,
                    timeout_seconds=self.config.deployment.health_check_timeout_seconds,
                ).as_dict()
        except InstallerError:
            result["health"] = {"status": "unknown"}
        return result

    def upgrade(self) -> ApplyResult:
        state = load_state(self.config.installation.name)
        if state is None:
            raise InstallerError("Installation not found.", stage="upgrade")
        enabled = self.config.enabled_service_names()
        name = self.config.installation.name
        with InstallationLock(name):
            outputs = self.provider.terraform.read_outputs()
            extra = build_ansible_extra_vars(self.config, outputs)
            inventory = installation_dir(name) / "inventory.ini"
            write_inventory(path=inventory, outputs=outputs, config=self.config)
            self.ansible.run_playbook("upgrade.yml", inventory=inventory, extra_vars=extra)
            health: dict[str, str] = {"status": "skipped"}
            url = platform_url(self.config, outputs)
            if "cloud_app" in enabled:
                report = self.health.verify(
                    url,
                    timeout_seconds=self.config.deployment.health_check_timeout_seconds,
                )
                health = report.as_dict()
            state.platform_version = self.config.platform.version
            state.enabled_services = enabled
            state.updated_at = datetime.now(UTC)
            save_state(state)
            return ApplyResult(state=state, outputs=outputs, health=health)

    def destroy(self) -> None:
        name = self.config.installation.name
        with InstallationLock(name):
            try:
                state = load_state(name)
                if state and state.public_ip:
                    outputs = TerraformOutputs(
                        instance_id=state.instance_id or "",
                        public_ip=state.public_ip,
                        elastic_ip=state.elastic_ip or "",
                        private_ip="",
                        region=state.region,
                        ssh_username="ubuntu",
                        security_group_id="",
                    )
                    inventory = installation_dir(name) / "inventory.ini"
                    write_inventory(path=inventory, outputs=outputs, config=self.config)
                    extra = build_ansible_extra_vars(self.config, outputs)
                    self.ansible.run_playbook(
                        "destroy.yml",
                        inventory=inventory,
                        extra_vars=extra,
                    )
            except InstallerError:
                logger.warning("Remote destroy playbook skipped or failed.")
            self.provider.destroy()
            state_path = installation_state_file(name)
            state_path.unlink(missing_ok=True)

    def _apply_infrastructure(self) -> TerraformOutputs:
        self.provider.terraform.init()
        return self.provider.apply()

    def _wait_for_ssh(self, outputs: TerraformOutputs) -> None:
        key_path = Path(self.config.aws.ssh_private_key_path).expanduser()
        wait_for_server(
            outputs.connect_ip,
            outputs.ssh_username,
            key_path,
            timeout_seconds=self.config.deployment.health_check_timeout_seconds,
        )

    def _run_site(self, outputs: TerraformOutputs) -> None:
        inventory = installation_dir(self.config.installation.name) / "inventory.ini"
        write_inventory(path=inventory, outputs=outputs, config=self.config)
        extra = build_ansible_extra_vars(self.config, outputs)
        self.ansible.run_playbook("site.yml", inventory=inventory, extra_vars=extra)

    def _save_state(
        self,
        outputs: TerraformOutputs,
        url: str | None,
        health: dict[str, str],
        enabled_services: list[str],
    ) -> InstallationState:
        existing = load_state(self.config.installation.name)
        state = existing or InstallationState(
            name=self.config.installation.name,
            provider=self.config.installation.provider,
            environment=self.config.installation.environment,
            region=outputs.region,
            platform_version=self.config.platform.version,
        )
        state.instance_id = outputs.instance_id
        state.public_ip = outputs.public_ip
        state.elastic_ip = outputs.elastic_ip or None
        state.platform_url = url
        state.platform_version = self.config.platform.version
        state.installed_components = self.config.enabled_component_names()
        state.enabled_services = enabled_services
        save_state(state)
        return state
