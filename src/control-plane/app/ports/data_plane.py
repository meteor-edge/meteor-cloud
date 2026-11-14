"""Application data-plane port: the control plane authorizes commands, the data plane delivers them."""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class DataPlaneApi(Protocol):
    def create_command(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def get_command(self, *, organization_id: uuid.UUID, command_id: uuid.UUID) -> dict[str, Any] | None: ...
