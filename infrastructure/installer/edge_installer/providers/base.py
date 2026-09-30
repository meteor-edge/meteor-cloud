"""Infrastructure provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from edge_installer.providers.aws.outputs import TerraformOutputs


class InfrastructureProvider(ABC):
    name: str

    @abstractmethod
    def validate(self) -> None: ...

    @abstractmethod
    def plan(self) -> dict[str, Any]: ...

    @abstractmethod
    def apply(self) -> TerraformOutputs: ...

    @abstractmethod
    def inspect(self) -> dict[str, Any]: ...

    @abstractmethod
    def destroy(self) -> None: ...
