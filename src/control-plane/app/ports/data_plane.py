"""Application data-plane port.

The control plane authorizes; the data plane delivers commands and serves stored
device telemetry.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class DataPlaneApi(Protocol):
    def create_command(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def get_command(self, *, organization_id: uuid.UUID, command_id: uuid.UUID) -> dict[str, Any] | None: ...

    def get_device_metrics(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        params: dict[str, str],
    ) -> dict[str, Any]: ...

    def write_events(self, events: list[dict[str, Any]]) -> dict[str, Any]: ...

    def list_events(self, *, organization_id: uuid.UUID, params: dict[str, str]) -> dict[str, Any]: ...

    def online_devices(self, *, organization_id: uuid.UUID, params: dict[str, str]) -> dict[str, Any]: ...
