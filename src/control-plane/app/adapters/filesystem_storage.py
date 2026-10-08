"""Local filesystem object storage adapter (a directory or mounted volume).

Suitable for single-node installs, development, and Kubernetes with a
PersistentVolume. Several API replicas need a shared (ReadWriteMany) volume.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.core.config import Settings
from app.ports.storage import StoredObject, StoredObjectNotFoundError

_CHUNK_SIZE = 1024 * 1024


class FilesystemObjectStorage:
    def __init__(self, settings: Settings) -> None:
        """Use ``OBJECT_STORAGE_PATH`` as the root; it is created on first write."""
        self._root = Path(settings.object_storage_path).resolve()

    def put(self, key: str, data: BinaryIO, *, content_type: str | None = None) -> None:
        """Write to a temporary file and rename it so readers never see partial content."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                shutil.copyfileobj(data, handle, _CHUNK_SIZE)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> StoredObject:
        """Open ``key`` for streaming. The content type is kept in PostgreSQL, not here."""
        path = self._path(key)
        try:
            handle = path.open("rb")
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise StoredObjectNotFoundError(key) from exc
        size = os.fstat(handle.fileno()).st_size

        def _chunks():
            with handle:
                while chunk := handle.read(_CHUNK_SIZE):
                    yield chunk

        return StoredObject(size=size, content_type=None, chunks=_chunks())

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def _path(self, key: str) -> Path:
        """Map a key to a path under the root, rejecting keys that escape it."""
        path = (self._root / key).resolve()
        if path == self._root or not path.is_relative_to(self._root):
            raise ValueError(f"Invalid object key: {key!r}")
        return path
