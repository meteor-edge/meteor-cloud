"""Service package."""

from edge_installer.services.registry import (
    SERVICES,
    all_service_names,
    get_service,
    resolve_enabled_services,
)

__all__ = [
    "SERVICES",
    "all_service_names",
    "get_service",
    "resolve_enabled_services",
]
