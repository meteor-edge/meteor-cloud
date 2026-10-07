"""Object storage port for large binary content (artifacts today).

The current adapter is S3-compatible (``S3ObjectStorage``): MinIO for self-hosted
deployments, AWS S3, or any other S3 API. PostgreSQL stores only the object key.

A future container registry is expected to run its own OCI distribution service
on the same storage backend (e.g. a separate bucket); it does not go through
this port.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Protocol


class StoredObjectNotFoundError(Exception):
    """Raised when an object key does not exist in storage."""


@dataclass
class StoredObject:
    """An open object. ``chunks`` streams the content and must be consumed once."""

    size: int
    content_type: str | None
    chunks: Iterator[bytes]


class ObjectStorage(Protocol):
    def put(self, key: str, data: BinaryIO, *, content_type: str | None = None) -> None:
        """Store ``data`` (read until EOF) under ``key``, replacing any existing object."""
        ...

    def get(self, key: str) -> StoredObject:
        """Open ``key`` for streaming. Raises ``StoredObjectNotFoundError``."""
        ...

    def delete(self, key: str) -> None:
        """Delete ``key``. Deleting a missing key is not an error."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether an object exists at key."""
        ...
