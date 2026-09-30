"""Terraform output models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TerraformOutputs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instance_id: str
    public_ip: str
    elastic_ip: str = ""
    private_ip: str
    region: str
    ssh_username: str = "ubuntu"
    security_group_id: str

    @property
    def connect_ip(self) -> str:
        return self.elastic_ip or self.public_ip
