"""Terraform output models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TerraformOutputs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instance_id: str = ""
    public_ip: str = ""
    elastic_ip: str = ""
    private_ip: str = ""
    region: str
    ssh_username: str = "ubuntu"
    security_group_id: str = ""
    # Set when services.vpn.placement is dedicated.
    vpn_gateway_instance_id: str = ""
    vpn_gateway_public_ip: str = ""
    vpn_gateway_private_ip: str = ""

    @property
    def connect_ip(self) -> str:
        return self.elastic_ip or self.public_ip
