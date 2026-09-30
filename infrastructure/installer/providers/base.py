"""Infrastructure provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class InfrastructureProvider(ABC):
    """Responsible for provisioning and managing infrastructure.

    Implementations must not install platform software. That is the job of
    PlatformComponent and PlatformDeployment.
    """

    name: str

    @abstractmethod
    def validate(self) -> None:
        """Validate provider credentials and configuration."""

    @abstractmethod
    def plan(self) -> dict[str, Any]:
        """Return a plan of infrastructure changes without applying them."""

    @abstractmethod
    def apply(self) -> dict[str, Any]:
        """Create or update infrastructure resources."""

    @abstractmethod
    def inspect(self) -> dict[str, Any]:
        """Return the current state of managed infrastructure."""

    @abstractmethod
    def destroy(self) -> dict[str, Any]:
        """Destroy managed infrastructure resources."""
