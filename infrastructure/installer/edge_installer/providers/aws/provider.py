"""AWS EC2 infrastructure provider."""

from __future__ import annotations

import logging
from typing import Any

from edge_installer.config.models import InstallationConfig
from edge_installer.exceptions import InfrastructureProvisioningError
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.providers.aws.terraform import terraform_runner_for
from edge_installer.providers.base import InfrastructureProvider

logger = logging.getLogger(__name__)


class AwsEc2Provider(InfrastructureProvider):
    name = "aws"

    def __init__(self, config: InstallationConfig) -> None:
        self.config = config
        self.terraform = terraform_runner_for(config)

    def validate(self) -> None:
        self.terraform.init()
        self.terraform.validate()

    def plan(self) -> dict[str, Any]:
        self.terraform.init()
        output = self.terraform.plan()
        return {
            "provider": self.name,
            "region": self.config.aws.region,
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
