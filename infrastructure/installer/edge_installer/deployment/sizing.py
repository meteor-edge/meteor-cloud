"""Pick an EC2 size from the containers make aws-up will start.

Does not shrink a type the operator already set larger. Unknown types (m5.large,
…) are left alone. ``aws.instance_type: auto`` always uses the recommendation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from edge_installer.config.models import InstallationConfig

logger = logging.getLogger(__name__)

# Typical working set (MiB), not docker mem_limit.
_CONTAINER_MIB: dict[str, int] = {
    "os_docker": 450,
    "traefik": 80,
    "postgres": 220,
    "backend": 350,
    "console": 40,
    "redis": 50,
    "minio": 200,
    "data_plane": 150,
    "emqx": 400,
    "observability": 900,
    "git_build": 500,
}

# Usable RAM for the family we launch. Leave ~15% for page cache / spikes.
_TYPE_MIB: dict[str, int] = {
    "t3.micro": 1024,
    "t3.small": 2048,
    "t3.medium": 4096,
    "t3.large": 8192,
    "t3.xlarge": 16384,
    "t4g.micro": 1024,
    "t4g.small": 2048,
    "t4g.medium": 4096,
    "t4g.large": 8192,
    "t4g.xlarge": 16384,
}

_AMD64 = ("t3.micro", "t3.small", "t3.medium", "t3.large", "t3.xlarge")
_ARM64 = ("t4g.micro", "t4g.small", "t4g.medium", "t4g.large", "t4g.xlarge")
_USABLE = 0.85


def _at_least(family: tuple[str, ...], current: str, minimum: str) -> str:
    if family.index(minimum) > family.index(current):
        return minimum
    return current


@dataclass(frozen=True)
class HostSize:
    instance_type: str
    root_volume_size_gb: int
    ram_mib: int
    reasons: tuple[str, ...]


def workload_units(config: InstallationConfig) -> list[str]:
    units = ["os_docker"]
    if not config.services.cloud_app.enabled:
        return units
    units.extend(["backend", "console"])
    if config.components.reverse_proxy.enabled:
        units.append("traefik")
    if config.components.postgres.enabled:
        units.append("postgres")
    if config.components.redis.enabled:
        units.append("redis")
    if config.observability.enabled:
        units.append("observability")
    if config.deployment.image_source == "git":
        units.append("git_build")
    return units


def estimate_ram_mib(config: InstallationConfig) -> tuple[int, tuple[str, ...]]:
    units = workload_units(config)
    total = sum(_CONTAINER_MIB[name] for name in units)
    return total, tuple(units)


def estimate_disk_gb(config: InstallationConfig) -> int:
    disk = 20
    if config.services.cloud_app.enabled:
        disk += 10
    if config.deployment.image_source == "git":
        disk += 10
    if config.observability.enabled:
        disk += 10
    return disk


def recommend_host_size(config: InstallationConfig) -> HostSize:
    ram_mib, reasons = estimate_ram_mib(config)
    family = _ARM64 if config.aws and config.aws.architecture == "arm64" else _AMD64
    chosen = family[-1]
    for name in family:
        if int(_TYPE_MIB[name] * _USABLE) >= ram_mib:
            chosen = name
            break
    if config.services.cloud_app.enabled and chosen.endswith(".micro"):
        chosen = family[1]  # never put the API on a 1 GiB host
    # Docker build and the Grafana stack need headroom the working-set sum misses.
    if "git_build" in reasons:
        chosen = _at_least(family, chosen, family[2])
    if "observability" in reasons:
        chosen = _at_least(family, chosen, family[3])
    return HostSize(
        instance_type=chosen,
        root_volume_size_gb=estimate_disk_gb(config),
        ram_mib=ram_mib,
        reasons=reasons,
    )


def resolve_aws_host(config: InstallationConfig) -> tuple[str, int, HostSize]:
    """Instance type and disk Terraform should use."""
    recommended = recommend_host_size(config)
    if config.aws is None:
        return recommended.instance_type, recommended.root_volume_size_gb, recommended

    configured = config.aws.instance_type.strip() or "auto"
    if configured == "auto":
        instance_type = recommended.instance_type
    else:
        have = _TYPE_MIB.get(configured)
        need = _TYPE_MIB.get(recommended.instance_type)
        if have is not None and need is not None and have < need:
            logger.info(
                "Raising instance type %s → %s (need ~%s MiB for %s)",
                configured,
                recommended.instance_type,
                recommended.ram_mib,
                ", ".join(recommended.reasons),
            )
            instance_type = recommended.instance_type
        else:
            instance_type = configured

    disk = max(config.aws.root_volume_size_gb, recommended.root_volume_size_gb)
    if disk > config.aws.root_volume_size_gb:
        logger.info(
            "Raising root volume %s → %s GiB for %s",
            config.aws.root_volume_size_gb,
            disk,
            ", ".join(recommended.reasons),
        )
    return instance_type, disk, recommended
