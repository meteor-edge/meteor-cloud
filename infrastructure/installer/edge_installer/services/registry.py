"""Modular installable services (cloud_app, vpn, ...)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    description: str
    terraform: bool
    ansible_deploy_playbook: str
    requires: tuple[str, ...] = ()
    default_enabled: bool = True


SERVICES: dict[str, ServiceDefinition] = {
    "cloud_app": ServiceDefinition(
        name="cloud_app",
        description="Edge Platform cloud application (Docker, Traefik, Postgres, Redis)",
        terraform=True,
        ansible_deploy_playbook="services/cloud_app.yml",
    ),
    "vpn": ServiceDefinition(
        name="vpn",
        description="WireGuard VPN tunnel on the cloud host",
        terraform=True,
        ansible_deploy_playbook="services/vpn.yml",
        requires=("cloud_app",),
    ),
}


def all_service_names() -> list[str]:
    return list(SERVICES.keys())


def get_service(name: str) -> ServiceDefinition:
    if name not in SERVICES:
        raise KeyError(f"Unknown service: {name}")
    return SERVICES[name]


def resolve_enabled_services(enabled: dict[str, bool]) -> list[str]:
    """Return ordered service names respecting dependencies."""
    selected = [name for name, on in enabled.items() if on and name in SERVICES]
    ordered: list[str] = []
    for name in selected:
        _add_with_dependencies(name, selected, ordered)
    return ordered


def _add_with_dependencies(name: str, selected: list[str], ordered: list[str]) -> None:
    if name in ordered:
        return
    definition = SERVICES[name]
    for dependency in definition.requires:
        if dependency in selected:
            _add_with_dependencies(dependency, selected, ordered)
    ordered.append(name)
