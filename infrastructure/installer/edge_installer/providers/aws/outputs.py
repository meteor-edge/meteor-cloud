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
    platform_url: str = ""
    backend_service_url: str = ""
    frontend_service_url: str = ""
    sql_connection_name: str = ""
    artifact_registry_url: str = ""
    project_id: str = ""
    load_balancer_ip: str = ""

    @property
    def connect_ip(self) -> str:
        return self.elastic_ip or self.public_ip
