"""In-memory ``ObjectStorage`` used by tests."""

from __future__ import annotations

from typing import BinaryIO

from app.ports.storage import StoredObject, StoredObjectNotFoundError


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str | None]] = {}
        self.fail_puts = False

    def put(self, key: str, data: BinaryIO, *, content_type: str | None = None) -> None:
        if self.fail_puts:
            raise RuntimeError("storage offline")
        chunks = []
        while chunk := data.read(64 * 1024):
            chunks.append(chunk)
        self.objects[key] = (b"".join(chunks), content_type)

    def get(self, key: str) -> StoredObject:
        if key not in self.objects:
            raise StoredObjectNotFoundError(key)
        content, content_type = self.objects[key]
        return StoredObject(size=len(content), content_type=content_type, chunks=iter([content]))

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects
