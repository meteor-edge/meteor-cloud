"""Deprecated OTA port.

Prefer ``app.ports.deployment.DeploymentProvider``. This module re-exports the
new port so older imports keep working until callers are updated.
"""

from __future__ import annotations

from app.ports.deployment import DeploymentProvider as OTAProvider

__all__ = ["OTAProvider"]
