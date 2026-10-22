"""Device private-network connectivity port.

MeteorCloud owns peer identity, IPAM, and revocation. The provider names the
transport (WireGuard today). Another transport can implement this port later.
"""

from __future__ import annotations

from typing import Protocol


class DeviceConnectivityProvider(Protocol):
    """Validate and describe a connectivity transport. IPAM stays in the service."""

    name: str

    def validate_public_key(self, public_key: str) -> str:
        """Return a normalized public key or raise ValueError."""
        ...

    def client_allowed_ips(self, address: str) -> str:
        """AllowedIPs value for the server peer entry of this device."""
        ...
