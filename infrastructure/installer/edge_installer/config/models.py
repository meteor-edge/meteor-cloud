"""Pydantic models for installer configuration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

INSTALLATION_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InstallationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    environment: Literal["development", "staging", "production"]
    provider: Literal["aws"] = "aws"
    demo: bool = False


class PlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    domain: str | None = None
    public_url: str | None = None


class AwsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str = Field(min_length=1)
    availability_zone: str | None = None
    # auto: pick t3/t4g from enabled containers. A smaller explicit type is raised.
    instance_type: str = "auto"
    architecture: Literal["amd64", "arm64"] = "amd64"
    ami_id: str | None = None
    ssh_key_name: str = Field(min_length=1)
    ssh_private_key_path: str = Field(min_length=1)
    root_volume_size_gb: int = Field(default=30, ge=8, le=1024)
    assign_elastic_ip: bool = True
    profile: str | None = None
    # With platform.domain, Terraform creates A records in this hosted zone.
    dns_zone_id: str = ""


class NetworkSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_ssh_cidrs: list[str] = Field(default_factory=list)
    allow_http: bool = True
    allow_https: bool = True


class PostgresComponentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: Literal["local"] = "local"
    database_name: str = "edge_platform"
    username: str = "edge_platform"


class RedisComponentSettings(BaseModel):
    """Bundled Redis for shared rate limits; off means in-memory limits (one API process)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
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

    repository_url: str = "https://github.com/meteor-edge/meteor-cloud.git"
    git_ref: str = "master"
    image_source: Literal["git", "registry"] = "git"
    backend_image: str = Field(min_length=1)
    frontend_image: str = Field(min_length=1)
    # Older installation.yaml files may still set these. Console source is in this repo.
    ui_repository_url: str = ""
    ui_git_ref: str = "main"
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
    # same_host: WireGuard on the cloud_app instance. dedicated: its own EC2 instance
    # (Terraform module vpn_gateway), joined to the API host by a WireGuard site link.
    placement: Literal["same_host", "dedicated"] = "same_host"
    instance_type: str = "t3.micro"
    root_volume_size_gb: int = Field(default=16, ge=8, le=1024)


class MqttServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # bundled: EMQX on the cloud_app instance (TLS 8883). external: devices use broker_host.
    enabled: bool = False
    broker: Literal["bundled", "external"] = "bundled"
    broker_host: str = ""
    broker_port: int = Field(default=8883, ge=1, le=65535)
    allowed_client_cidrs: list[str] = Field(default_factory=lambda: ["0.0.0.0/0"])

    @model_validator(mode="after")
    def _check_broker(self) -> MqttServiceSettings:
        if self.enabled and self.broker == "external" and not self.broker_host.strip():
            raise ValueError("services.mqtt.broker_host is required for an external broker")
        if self.enabled and self.broker == "bundled" and self.broker_port != 8883:
            raise ValueError("services.mqtt.broker_port must be 8883 for the bundled broker")
        return self


class ServicesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud_app: CloudAppServiceSettings = Field(default_factory=CloudAppServiceSettings)
    vpn: VpnServiceSettings = Field(default_factory=VpnServiceSettings)
    # Deployed by Ansible with cloud_app, not a separate Terraform service.
    mqtt: MqttServiceSettings = Field(default_factory=MqttServiceSettings)

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
    aws: AwsSettings | None = None
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

    @model_validator(mode="after")
    def require_provider_settings(self) -> InstallationConfig:
        provider = self.installation.provider
        if provider == "aws" and self.aws is None:
            raise ValueError("aws settings are required when installation.provider is aws")
        return self
