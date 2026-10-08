"""Unit tests for the filesystem object storage adapter and provider selection."""

from io import BytesIO

import pytest

from app.adapters import object_storage, rate_limiter
from app.adapters.filesystem_storage import FilesystemObjectStorage
from app.core.config import Settings
from app.devices.rate_limit import InMemoryRateLimiter
from app.ports.storage import StoredObjectNotFoundError


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None).model_copy(
        update={"object_storage_provider": "filesystem", "object_storage_path": str(tmp_path / "artifacts")}
    )


def test_provider_selection_returns_filesystem_adapter(settings):
    assert isinstance(object_storage(settings), FilesystemObjectStorage)


def test_put_get_exists_delete_round_trip(settings, tmp_path):
    storage = FilesystemObjectStorage(settings)
    content = b"x" * (3 * 1024 * 1024 + 7)
    storage.put("org/artifact/image.img", BytesIO(content), content_type="application/octet-stream")

    assert storage.exists("org/artifact/image.img") is True
    stored = storage.get("org/artifact/image.img")
    assert stored.size == len(content)
    assert b"".join(stored.chunks) == content
    assert not [p for p in (tmp_path / "artifacts" / "org" / "artifact").iterdir() if p.name.startswith(".upload-")]

    storage.delete("org/artifact/image.img")
    assert storage.exists("org/artifact/image.img") is False
    storage.delete("org/artifact/image.img")


def test_put_replaces_existing_object(settings):
    storage = FilesystemObjectStorage(settings)
    storage.put("key", BytesIO(b"old"))
    storage.put("key", BytesIO(b"new"))
    assert b"".join(storage.get("key").chunks) == b"new"


def test_missing_object_raises_port_error(settings):
    with pytest.raises(StoredObjectNotFoundError):
        FilesystemObjectStorage(settings).get("missing")


def test_failed_write_leaves_no_partial_file(settings, tmp_path):
    class Broken:
        def read(self, _size=-1):
            raise OSError("client disconnected")

    storage = FilesystemObjectStorage(settings)
    with pytest.raises(OSError):
        storage.put("dir/key", Broken())
    assert list((tmp_path / "artifacts" / "dir").iterdir()) == []


@pytest.mark.parametrize("key", ["../escape", "a/../../escape", "/etc/passwd", ""])
def test_keys_outside_root_are_rejected(settings, key):
    storage = FilesystemObjectStorage(settings)
    with pytest.raises(ValueError):
        storage.put(key, BytesIO(b"data"))


def test_memory_cache_provider_needs_no_redis():
    settings = Settings(_env_file=None).model_copy(update={"cache_provider": "memory"})
    limiter = rate_limiter(settings, limit=1, window_seconds=60, prefix="test")
    assert isinstance(limiter, InMemoryRateLimiter)
    assert limiter.allow("10.0.0.1") is True
    assert limiter.allow("10.0.0.1") is False


def test_in_memory_limiter_drops_expired_windows(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr("app.devices.rate_limit.time.time", lambda: now[0])
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    for index in range(100):
        limiter.allow(f"10.0.0.{index}")
    now[0] += 60
    assert limiter.allow("10.0.0.1") is True
    assert len(limiter._counters) == 1
