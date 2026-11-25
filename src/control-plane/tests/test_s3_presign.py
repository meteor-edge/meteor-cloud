"""S3 presigned download URLs are signed for an endpoint devices can reach."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.adapters.filesystem_storage import FilesystemObjectStorage
from app.adapters.s3_storage import S3ObjectStorage
from app.core.config import Settings


def _settings(**values) -> Settings:
    base = {
        "OBJECT_STORAGE_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret-example",
        "OBJECT_STORAGE_BUCKET": "artifacts",
        "OBJECT_STORAGE_REGION": "eu-central-1",
    }
    return Settings(**(base | values))


def test_aws_s3_is_presigned_without_contacting_storage() -> None:
    url = S3ObjectStorage(_settings()).presigned_get_url("org/a.bin", expires_in=600, file_name="ä.bin")
    assert url is not None
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.path.endswith("/artifacts/org/a.bin")
    query = parse_qs(parts.query)
    assert query["X-Amz-Expires"] == ["600"]
    assert "attachment" in query["response-content-disposition"][0]


def test_private_endpoint_without_a_public_one_streams() -> None:
    storage = S3ObjectStorage(_settings(OBJECT_STORAGE_ENDPOINT_URL="http://minio:9000"))
    assert storage.presigned_get_url("k", expires_in=600) is None


def test_public_endpoint_is_used_for_signing() -> None:
    storage = S3ObjectStorage(
        _settings(
            OBJECT_STORAGE_ENDPOINT_URL="http://minio:9000",
            OBJECT_STORAGE_PUBLIC_ENDPOINT_URL="https://s3.example.com",
        )
    )
    url = storage.presigned_get_url("k", expires_in=600)
    assert url is not None and url.startswith("https://s3.example.com/artifacts/k?")


def test_filesystem_storage_has_no_url(tmp_path) -> None:
    storage = FilesystemObjectStorage(_settings(OBJECT_STORAGE_PATH=str(tmp_path)))
    assert storage.presigned_get_url("k", expires_in=600) is None


@pytest.mark.parametrize("ttl", [10, 8 * 24 * 3600])
def test_url_lifetime_is_bounded(ttl: int) -> None:
    with pytest.raises(ValueError):
        _settings(ARTIFACT_DOWNLOAD_URL_TTL_SECONDS=ttl)
