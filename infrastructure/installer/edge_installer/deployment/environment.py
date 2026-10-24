"""Environment variable assembly for deployment."""

from __future__ import annotations

import os

from edge_installer.config.models import InstallationConfig
from edge_installer.providers.aws.outputs import TerraformOutputs


def platform_url(config: InstallationConfig, outputs: TerraformOutputs) -> str:
    if config.platform.public_url:
        return config.platform.public_url.rstrip("/")
    if config.platform.domain:
        scheme = "https" if config.network.allow_https else "http"
        return f"{scheme}://{config.platform.domain}"
    return f"http://{outputs.connect_ip}"


def build_ansible_extra_vars(
    config: InstallationConfig,
    outputs: TerraformOutputs,
) -> dict[str, str]:
    url = platform_url(config, outputs)
    enabled = config.enabled_service_names()
    extra: dict[str, str] = {
        "installation_name": config.installation.name,
        "platform_version": config.platform.version,
        "platform_domain": config.platform.domain or "",
        "platform_public_url": url,
        "enabled_services": enabled,
        "demo_data": str(config.installation.demo).lower(),
        "repository_url": config.deployment.repository_url,
        "git_ref": config.deployment.git_ref,
        "image_source": config.deployment.image_source,
        "backend_image": config.deployment.backend_image,
        "frontend_image": config.deployment.frontend_image,
        "image_pull_policy": config.deployment.image_pull_policy,
        "postgres_provider": "bundled" if config.components.postgres.enabled else "external",
        "cache_provider": "redis" if config.components.redis.enabled else "memory",
        "postgres_database": config.components.postgres.database_name,
        "postgres_username": config.components.postgres.username,
        "platform_env": config.installation.environment,
        "observability_enabled": str(config.observability.enabled).lower(),
        "observability_backend": config.observability.backend,
        "log_format": "json" if config.observability.enabled else "console",
        "vpn_listen_port": str(config.services.vpn.listen_port),
        "vpn_network_cidr": config.services.vpn.network_cidr,
        "vpn_server_address": config.services.vpn.server_address,
        **_mqtt_extra_vars(config),
    }
    # Ansible derives CONNECTIVITY_PROVIDER and the device endpoint from the gateway
    # host (generating its key when none is given); extra-vars would override that.
    vpn_key = os.environ.get("EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY", "")
    if vpn_key:
        extra["vpn_server_private_key"] = vpn_key
    if outputs.vpn_gateway_private_ip:
        # The API host reaches a dedicated gateway inside the VPC.
        extra["vpn_link_endpoint"] = f"{outputs.vpn_gateway_private_ip}:{config.services.vpn.listen_port}"
    vpn_pub = os.environ.get("EDGE_PLATFORM_VPN_SERVER_PUBLIC_KEY", "").strip()
    if vpn_pub:
        extra["wireguard_server_public_key"] = vpn_pub
    connectivity_token = os.environ.get("EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN", "").strip()
    if connectivity_token:
        extra["connectivity_internal_token"] = connectivity_token
    if db_url := os.environ.get("EDGE_PLATFORM_DATABASE_URL", "").strip():
        extra["external_database_url"] = db_url
    return extra


def _mqtt_extra_vars(config: InstallationConfig) -> dict[str, str]:
    mqtt = config.services.mqtt
    if not mqtt.enabled:
        return {"mqtt_enabled": "false"}
    extra = {
        "mqtt_enabled": "true",
        "mqtt_broker": mqtt.broker,
        "mqtt_broker_port": str(mqtt.broker_port),
    }
    # Bundled: the role defaults reach EMQX inside Compose and advertise the platform host.
    if mqtt.broker == "external":
        extra["mqtt_broker_host"] = mqtt.broker_host
        extra["mqtt_public_host"] = mqtt.broker_host
    return extra


def secret_env_vars() -> dict[str, str]:
    keys = (
        "EDGE_PLATFORM_POSTGRES_PASSWORD",
        "EDGE_PLATFORM_DATABASE_URL",
        "EDGE_PLATFORM_JWT_SECRET",
        "EDGE_PLATFORM_ADMIN_EMAIL",
        "EDGE_PLATFORM_ADMIN_PASSWORD",
        "EDGE_PLATFORM_ACME_EMAIL",
        "EDGE_PLATFORM_REDIS_PASSWORD",
        "EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD",
        "EDGE_PLATFORM_MQTT_INTERNAL_TOKEN",
        "APP_SECRET_KEY",
    )
    return {key: value for key in keys if (value := os.environ.get(key))}
