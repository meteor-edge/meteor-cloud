"""Extended configuration validation."""

from __future__ import annotations

import os
from pathlib import Path

from edge_installer.config.models import INSTALLATION_NAME_PATTERN, InstallationConfig
from edge_installer.exceptions import ConfigurationError
from edge_installer.process.runner import command_exists

REQUIRED_SECRET_VARS = (
    "EDGE_PLATFORM_POSTGRES_PASSWORD",
    "EDGE_PLATFORM_JWT_SECRET",
)


OPTIONAL_SECRET_VARS = (
    "EDGE_PLATFORM_ADMIN_EMAIL",
    "EDGE_PLATFORM_ADMIN_PASSWORD",
    "EDGE_PLATFORM_ACME_EMAIL",
    "EDGE_PLATFORM_REDIS_PASSWORD",
)


def validate_configuration(config: InstallationConfig) -> list[str]:
    errors: list[str] = []
    provider = config.installation.provider

    if not INSTALLATION_NAME_PATTERN.fullmatch(config.installation.name):
        errors.append(
            "installation.name must be lowercase alphanumeric with optional hyphens"
        )

    if provider == "aws":
        if config.aws is None:
            errors.append("aws settings are required when installation.provider is aws")
        else:
            key_path = Path(config.aws.ssh_private_key_path).expanduser()
            if not key_path.exists():
                errors.append(f"aws.ssh_private_key_path does not exist: {key_path}")
        if not config.network.allowed_ssh_cidrs:
            errors.append("network.allowed_ssh_cidrs must not be empty")

    if provider == "gcp":
        if config.gcp is None:
            errors.append("gcp settings are required when installation.provider is gcp")
        if config.services.vpn.enabled:
            errors.append("services.vpn is not supported on GCP Cloud Run; set services.vpn.enabled=false")

    enabled_services = config.enabled_service_names()
    if not enabled_services:
        errors.append("At least one service must be enabled under services")

    if config.services.vpn.enabled and not config.services.cloud_app.enabled:
        errors.append("services.vpn requires services.cloud_app to be enabled")

    if config.services.cloud_app.enabled:
        if not config.components.postgres.enabled:
            errors.append("components.postgres must be enabled when cloud_app is enabled")
        if provider == "aws" and not config.components.reverse_proxy.enabled:
            errors.append("components.reverse_proxy must be enabled when cloud_app is enabled")
        if not config.deployment.backend_image.strip():
            errors.append("deployment.backend_image must be configured")
        if not config.deployment.frontend_image.strip():
            errors.append("deployment.frontend_image must be configured")
        for var in REQUIRED_SECRET_VARS:
            if not os.environ.get(var):
                errors.append(f"{var} is not set")

    if config.platform.domain and config.platform.public_url:
        errors.append("platform.domain and platform.public_url cannot both be set")

    if config.observability.enabled and config.observability.backend == "cloudwatch":
        errors.append(
            "observability.backend=cloudwatch is not implemented yet; "
            "use backend=prometheus or set observability.enabled=false"
        )

    return errors


def validate_dependencies(config: InstallationConfig | None = None) -> list[str]:
    errors: list[str] = []
    tools = ["terraform"]
    if config is None or config.installation.provider == "aws":
        tools.extend(["ansible-playbook", "ssh"])
    for tool in tools:
        if not command_exists(tool):
            errors.append(f"Required tool not found in PATH: {tool}")
    return errors


def validate_aws_credentials(profile: str | None = None) -> list[str]:
    if profile:
        if not os.environ.get("AWS_PROFILE"):
            os.environ["AWS_PROFILE"] = profile
    if not any(
        os.environ.get(key)
        for key in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    ):
        creds = Path.home() / ".aws" / "credentials"
        if not creds.exists():
            return ["AWS credentials are not configured"]
    return []


def validate_gcp_credentials() -> list[str]:
    adc_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if adc_env:
        path = Path(adc_env).expanduser()
        if not path.exists():
            return [f"GOOGLE_APPLICATION_CREDENTIALS does not exist: {path}"]
        return []
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc.exists():
        return []
    return [
        "GCP credentials are not configured "
        "(gcloud auth application-default login or GOOGLE_APPLICATION_CREDENTIALS)"
    ]


def ensure_valid(config: InstallationConfig) -> None:
    errors = validate_configuration(config) + validate_dependencies(config)
    if config.installation.provider == "aws":
        profile = config.aws.profile if config.aws else None
        errors.extend(validate_aws_credentials(profile))
    elif config.installation.provider == "gcp":
        errors.extend(validate_gcp_credentials())
    if errors:
        message = "Configuration is invalid:\n\n" + "\n".join(f"- {item}" for item in errors)
        raise ConfigurationError(message, stage="validation")
