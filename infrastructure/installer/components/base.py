"""Platform component interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PlatformComponent(ABC):
    """Responsible for installing and managing a single software component.

    Components are independent of how infrastructure was provisioned.
    """

    name: str

    @abstractmethod
    def validate(self) -> None:
        """Validate that the component can be installed."""

    @abstractmethod
    def install(self) -> dict[str, Any]:
        """Install the component."""

    @abstractmethod
    def configure(self) -> dict[str, Any]:
        """Apply component configuration."""

    @abstractmethod
    def upgrade(self) -> dict[str, Any]:
        """Upgrade the component to the desired version."""

    @abstractmethod
    def uninstall(self) -> dict[str, Any]:
        """Remove the component."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return component health information."""
