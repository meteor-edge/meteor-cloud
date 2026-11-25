"""Container registry port: the OCI service that stores images.

Images never pass through the control plane. An adapter only describes how to run the
registry so that it trusts our access tokens (docs/registry.md).
"""

from __future__ import annotations

from typing import Any, Protocol


class ContainerRegistry(Protocol):
    def config_files(self, jwks: dict[str, Any]) -> dict[str, str]:
        """File name -> content for the registry's configuration directory."""
        ...
