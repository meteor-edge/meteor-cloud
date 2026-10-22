"""Fleet deployments and per-device software state.

Desired vs installed software lives in ``device_software``. The Deploy action
writes desired state through ``DeploymentProvider`` and records progress in
``deployments`` / ``deployment_devices``.
"""

from app.deployments.models import Deployment, DeploymentDevice
from app.deployments.software_models import DeviceSoftware

__all__ = ["Deployment", "DeploymentDevice", "DeviceSoftware"]
