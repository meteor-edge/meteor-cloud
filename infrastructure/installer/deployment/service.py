"""Platform deployment service.

Coordinates infrastructure providers and platform components to install,
upgrade, and destroy the control-plane platform itself.
"""

from __future__ import annotations

import logging
from typing import Any

from components.registry import get_component
from config.models import InstallationConfig
from providers.registry import get_provider

logger = logging.getLogger(__name__)


class PlatformDeployment:
    """Orchestrates deploying the Edge Platform control plane.

    Milestone 1 provides the architecture and method signatures only.
    Concrete provisioning and installation arrive in later milestones.
    """

    def __init__(self, config: InstallationConfig) -> None:
        self.config = config
        self.provider = get_provider(config.installation.provider)
        self.components = self._enabled_components()

    def validate(self) -> None:
        """Validate provider and enabled components before any changes."""
        logger.info("Validating deployment for '%s'", self.config.installation.name)
        raise NotImplementedError("PlatformDeployment.validate() is not implemented yet")

    def plan(self) -> dict[str, Any]:
        """Return a deployment plan without applying changes."""
        logger.info("Planning deployment for '%s'", self.config.installation.name)
        raise NotImplementedError("PlatformDeployment.plan() is not implemented yet")

    def apply(self) -> dict[str, Any]:
        """Provision infrastructure and install enabled components."""
        logger.info("Applying deployment for '%s'", self.config.installation.name)
        raise NotImplementedError("PlatformDeployment.apply() is not implemented yet")

    def upgrade(self) -> dict[str, Any]:
        """Upgrade the platform and its components to the configured version."""
        logger.info("Upgrading deployment for '%s'", self.config.installation.name)
        raise NotImplementedError("PlatformDeployment.upgrade() is not implemented yet")

    def destroy(self) -> dict[str, Any]:
        """Uninstall components and destroy infrastructure."""
        logger.info("Destroying deployment for '%s'", self.config.installation.name)
        raise NotImplementedError("PlatformDeployment.destroy() is not implemented yet")

    def status(self) -> dict[str, Any]:
        """Return combined infrastructure and component status."""
        logger.info("Collecting status for '%s'", self.config.installation.name)
        raise NotImplementedError("PlatformDeployment.status() is not implemented yet")

    def _enabled_components(self):
        enabled = []
        settings = self.config.components
        mapping = {
            "postgres": settings.postgres.enabled,
            "redis": settings.redis.enabled,
            "reverse_proxy": settings.reverse_proxy.enabled,
        }
        for name, is_enabled in mapping.items():
            if is_enabled:
                enabled.append(get_component(name))
        return enabled
