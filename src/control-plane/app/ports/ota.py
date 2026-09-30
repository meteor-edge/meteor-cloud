"""OTA (firmware distribution) port.

No OTA provider is implemented. Configuration uses ``ota_provider=none``.
A future Mender (or other) adapter would implement this protocol.
"""

from __future__ import annotations

from typing import Protocol


class OTAProvider(Protocol):
    def create_deployment(self, device_id: str, artifact_ref: str) -> str:
        """Start a firmware deployment and return a provider-specific id."""
        ...
