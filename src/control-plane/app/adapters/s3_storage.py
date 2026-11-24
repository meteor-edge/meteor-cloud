"""S3-compatible object storage adapter (AWS S3, MinIO, Ceph, GCS interoperability, ...)."""

from __future__ import annotations

import threading
from typing import BinaryIO
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.ports.storage import StoredObject, StoredObjectNotFoundError

_CHUNK_SIZE = 1024 * 1024
_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


class S3ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        """Configure a client without contacting the bucket.

        Empty endpoint or credential settings use boto3 defaults. Client
        initialization errors propagate.
        """
        self._bucket = settings.object_storage_bucket
        self._region = settings.object_storage_region
        # Empty endpoint/credentials fall back to AWS defaults (e.g. an EC2 instance role).
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_endpoint_url or None,
            region_name=settings.object_storage_region or None,
            aws_access_key_id=settings.object_storage_access_key_id or None,
            aws_secret_access_key=settings.object_storage_secret_access_key or None,
            # Path-style addressing is required by MinIO and harmless for S3. boto3's default
            # flexible checksums are rejected by some S3-compatible services (e.g. GCS).
            config=Config(
                s3={"addressing_style": "path"},
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        # Presigned URLs carry the host they were signed for, so devices need one signed for
        # an endpoint they can reach. With a private endpoint and no public one, stream instead.
        public_endpoint = settings.object_storage_public_endpoint_url.strip()
        self._presign_client = None
        if public_endpoint:
            self._presign_client = boto3.client(
                "s3",
                endpoint_url=public_endpoint,
                region_name=settings.object_storage_region or None,
                aws_access_key_id=settings.object_storage_access_key_id or None,
                aws_secret_access_key=settings.object_storage_secret_access_key or None,
                config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
            )
        elif not settings.object_storage_endpoint_url:
            self._presign_client = self._client  # AWS S3: the default endpoint is public
        self._auto_create_bucket = settings.object_storage_auto_create_bucket
        self._bucket_ready = False
        self._lock = threading.Lock()

    def put(self, key: str, data: BinaryIO, *, content_type: str | None = None) -> None:
        """Upload from the stream's current position, replacing the object at key.

        Create the bucket first when configured to do so. Storage and stream-read
        errors propagate to the caller.
        """
        self._ensure_bucket()
        extra_args = {"ContentType": content_type} if content_type else None
        # upload_fileobj streams and switches to multipart upload for large files.
        self._client.upload_fileobj(data, self._bucket, key, ExtraArgs=extra_args)

    def get(self, key: str) -> StoredObject:
        """Open an object and return its byte size, content type, and chunk iterator.

        Raise StoredObjectNotFoundError for a missing key and propagate other
        storage errors. Errors during iteration propagate from the iterator,
        which closes the response body on exit once iteration has started.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _MISSING_CODES:
                raise StoredObjectNotFoundError(key) from exc
            raise
        body = response["Body"]

        def _chunks():
            """Yield content in chunks of up to 1 MiB and close the body on exit.

            Stream-read errors propagate to the consumer.
            """
            try:
                yield from body.iter_chunks(chunk_size=_CHUNK_SIZE)
            finally:
                body.close()

        return StoredObject(
            size=int(response["ContentLength"]),
            content_type=response.get("ContentType"),
            chunks=_chunks(),
        )

    def presigned_get_url(self, key: str, *, expires_in: int, file_name: str | None = None) -> str | None:
        """Sign a GET for ``key`` locally (no request to storage), or None when devices can't reach it."""
        if self._presign_client is None:
            return None
        params: dict = {"Bucket": self._bucket, "Key": key}
        if file_name:
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{quote(file_name)}"
        return self._presign_client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)

    def delete(self, key: str) -> None:
        """Delete the object, tolerating a missing key; propagate storage errors."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        """Return False for a missing key; propagate other storage errors."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _MISSING_CODES:
                return False
            raise
        return True

    def _ensure_bucket(self) -> None:
        """Check or create the bucket once when automatic creation is enabled.

        Propagate bucket access and creation errors without marking it ready.
        """
        if self._bucket_ready or not self._auto_create_bucket:
            return
        with self._lock:
            if self._bucket_ready:
                return
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if _error_code(exc) not in _MISSING_CODES:
                    raise
                params: dict = {"Bucket": self._bucket}
                if self._region and self._region != "us-east-1":
                    params["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
                self._client.create_bucket(**params)
            self._bucket_ready = True


def _error_code(exc: ClientError) -> str:
    """Return the storage error code as text, or an empty string if absent."""
    return str(exc.response.get("Error", {}).get("Code", ""))
