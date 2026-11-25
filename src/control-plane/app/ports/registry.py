"""Container registry port: the OCI service that stores images.

Layers never pass through the control plane. An adapter describes how to run the registry
so that it trusts our access tokens, and reads tags and manifests from it (docs/registry.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RegistryUnavailableError(Exception):
    """The registry did not answer, or answered with an error; trying again may help."""


@dataclass(frozen=True, slots=True)
class Platform:
    os: str
    architecture: str
    variant: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ImageManifest:
    digest: str
    media_type: str
    platforms: tuple[Platform, ...]

    @property
    def size_bytes(self) -> int:
        return sum(platform.size_bytes for platform in self.platforms)


class ContainerRegistry(Protocol):
    def config_files(self, jwks: dict[str, Any]) -> dict[str, str]:
        """File name -> content for the registry's configuration directory."""
        ...

    def list_repositories(self) -> list[str]: ...

    def list_tags(self, repository: str) -> list[str]:
        """Empty when the repository has no tags or does not exist."""
        ...

    def digest(self, repository: str, reference: str) -> str | None:
        """Digest of a tag (or digest), None when it does not exist."""
        ...

    def manifest(self, repository: str, reference: str) -> ImageManifest | None: ...

    def delete_tag(self, repository: str, tag: str) -> None:
        """A tag that is already gone is not an error."""
        ...
