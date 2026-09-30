"""GCP Cloud Run infrastructure provider."""

from __future__ import annotations

import logging
from typing import Any

from edge_installer.config.models import InstallationConfig
from edge_installer.exceptions import InfrastructureProvisioningError
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.providers.base import InfrastructureProvider
from edge_installer.providers.gcp.terraform import gcp_terraform_runner_for

logger = logging.getLogger(__name__)


class GcpCloudRunProvider(InfrastructureProvider):
    name = "gcp"

    def __init__(self, config: InstallationConfig) -> None:
        self.config = config
        self.terraform = gcp_terraform_runner_for(config)

    def validate(self) -> None:
        self.terraform.init()
        self.terraform.validate()

    def plan(self) -> dict[str, Any]:
        self.terraform.init()
        output = self.terraform.plan()
        region = self.config.gcp.region if self.config.gcp else ""
        return {
            "provider": self.name,
            "region": region,
            "plan_output": output,
            "components": self.config.enabled_component_names(),
            "platform_version": self.config.platform.version,
        }

    def apply(self) -> TerraformOutputs:
        self.terraform.init()
        try:
            return self.terraform.apply()
        except Exception as exc:
            raise InfrastructureProvisioningError(str(exc), stage="terraform_apply") from exc

    def inspect(self) -> dict[str, Any]:
        try:
            outputs = self.terraform.read_outputs()
        except Exception as exc:
            raise InfrastructureProvisioningError(str(exc), stage="terraform_inspect") from exc
        return outputs.model_dump()

    def destroy(self) -> None:
        self.terraform.destroy()
