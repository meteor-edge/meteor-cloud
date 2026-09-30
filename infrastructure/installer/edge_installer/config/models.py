"""Pydantic models for installer configuration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

INSTALLATION_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InstallationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    environment: Literal["development", "staging", "production"]
    provider: Literal["aws"] = "aws"


class PlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    domain: str | None = None
    public_url: str | None = None


class AwsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str = Field(min_length=1)
    availability_zone: str | None = None
    instance_type: str = "t3.small"
    architecture: Literal["amd64", "arm64"] = "amd64"
    ami_id: str | None = None
    ssh_key_name: str = Field(min_length=1)
    ssh_private_key_path: str = Field(min_length=1)
    root_volume_size_gb: int = Field(default=30, ge=8, le=1024)
    assign_elastic_ip: bool = True
    profile: str | None = None


class NetworkSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_ssh_cidrs: list[str] = Field(min_length=1)
    allow_http: bool = True
    allow_https: bool = True


class PostgresComponentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: Literal["local"] = "local"
    database_name: str = "edge_platform"
    username: str = "edge_platform"


class RedisComponentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: Literal["local"] = "local"


class ReverseProxyComponentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: Literal["traefik"] = "traefik"


class ComponentsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    postgres: PostgresComponentSettings = Field(default_factory=PostgresComponentSettings)
    redis: RedisComponentSettings = Field(default_factory=RedisComponentSettings)
    reverse_proxy: ReverseProxyComponentSettings = Field(
        default_factory=ReverseProxyComponentSettings
    )


class DeploymentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_url: str = "https://github.com/shahabkamali/MeteorCloud.git"
    git_ref: str = "master"
    image_source: Literal["git", "registry"] = "git"
    backend_image: str = Field(min_length=1)
    frontend_image: str = Field(min_length=1)
    image_pull_policy: Literal["always", "if-not-present", "never"] = "always"
    health_check_timeout_seconds: int = Field(default=180, ge=30, le=900)


class SecretsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["environment"] = "environment"


class ObservabilitySettings(BaseModel):
    """Ops telemetry sink. LOG_FORMAT setting selects console or JSON log output; /metrics endpoint is always available.

    ``prometheus`` runs Loki + Prometheus + Grafana on the instance.
    ``cloudwatch`` is reserved and rejected until that shipper is implemented.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    backend: Literal["prometheus", "cloudwatch"] = "prometheus"


class CloudAppServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class VpnServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    listen_port: int = Field(default=51820, ge=1024, le=65535)
    network_cidr: str = "10.8.0.0/24"
    allowed_client_cidrs: list[str] = Field(default_factory=lambda: ["0.0.0.0/0"])
    server_address: str = "10.8.0.1/24"


class ServicesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud_app: CloudAppServiceSettings = Field(default_factory=CloudAppServiceSettings)
    vpn: VpnServiceSettings = Field(default_factory=VpnServiceSettings)

    def enabled_map(self) -> dict[str, bool]:
        return {
            "cloud_app": self.cloud_app.enabled,
            "vpn": self.vpn.enabled,
        }

    def enabled_names(self) -> list[str]:
        from edge_installer.services.registry import resolve_enabled_services

        return resolve_enabled_services(self.enabled_map())


class InstallationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation: InstallationSettings
    platform: PlatformSettings
    aws: AwsSettings
    network: NetworkSettings
    services: ServicesSettings = Field(default_factory=ServicesSettings)
    components: ComponentsSettings = Field(default_factory=ComponentsSettings)
    deployment: DeploymentSettings
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    def enabled_component_names(self) -> list[str]:
        names: list[str] = []
        if self.components.postgres.enabled:
            names.append("postgres")
        if self.components.redis.enabled:
            names.append("redis")
        if self.components.reverse_proxy.enabled:
            names.append("reverse_proxy")
        return names

    def enabled_service_names(self) -> list[str]:
        return self.services.enabled_names()
