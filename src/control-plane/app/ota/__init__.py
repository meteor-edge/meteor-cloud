"""OTA / deployment provider facade.

Prefer ``DEPLOYMENT_PROVIDER`` and ``app.ports.deployment.DeploymentProvider``.
``OTA_PROVIDER=none`` remains accepted for older env files.
"""

from app.adapters import deployment_provider, ota_provider
from app.ports.deployment import DeploymentProvider
from app.ports.ota import OTAProvider

__all__ = ["DeploymentProvider", "OTAProvider", "deployment_provider", "ota_provider"]
