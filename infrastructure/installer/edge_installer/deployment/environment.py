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
        "repository_url": config.deployment.repository_url,
        "git_ref": config.deployment.git_ref,
        "image_source": config.deployment.image_source,
        "backend_image": config.deployment.backend_image,
        "frontend_image": config.deployment.frontend_image,
        "image_pull_policy": config.deployment.image_pull_policy,
        "postgres_database": config.components.postgres.database_name,
        "postgres_username": config.components.postgres.username,
        "platform_env": config.installation.environment,
        "observability_enabled": str(config.observability.enabled).lower(),
        "observability_backend": config.observability.backend,
        "log_format": "json" if config.observability.enabled else "console",
        "vpn_listen_port": str(config.services.vpn.listen_port),
        "vpn_network_cidr": config.services.vpn.network_cidr,
        "vpn_server_address": config.services.vpn.server_address,
    }
    vpn_key = os.environ.get("EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY", "")
    if vpn_key:
        extra["vpn_server_private_key"] = vpn_key
    return extra


def secret_env_vars() -> dict[str, str]:
    keys = (
        "EDGE_PLATFORM_POSTGRES_PASSWORD",
        "EDGE_PLATFORM_JWT_SECRET",
        "EDGE_PLATFORM_ADMIN_EMAIL",
        "EDGE_PLATFORM_ADMIN_PASSWORD",
        "EDGE_PLATFORM_ACME_EMAIL",
        "EDGE_PLATFORM_REDIS_PASSWORD",
        "APP_SECRET_KEY",
    )
    return {key: value for key in keys if (value := os.environ.get(key))}
