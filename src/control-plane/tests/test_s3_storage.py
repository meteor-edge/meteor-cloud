"""Unit tests for S3 error translation, streaming, and bucket initialization."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from app.adapters import object_storage
from app.adapters.s3_storage import S3ObjectStorage
from app.core.config import Settings
from app.ports.storage import StoredObjectNotFoundError


def storage_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "storage failure"}}, "TestOperation")


@pytest.fixture
def s3(monkeypatch):
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr("app.adapters.s3_storage.boto3.client", factory)

    def build(**overrides):
        settings = Settings(_env_file=None).model_copy(
            update={
                "object_storage_bucket": "unit-artifacts",
                "object_storage_auto_create_bucket": True,
                "object_storage_region": "us-east-1",
                **overrides,
            }
        )
        return S3ObjectStorage(settings)

    return build, client, factory


def test_client_configuration_is_lazy_and_supports_aws_defaults(s3):
    build, client, factory = s3
    build(object_storage_endpoint_url="", object_storage_access_key_id="", object_storage_secret_access_key="")
    client.head_bucket.assert_not_called()
    kwargs = factory.call_args.kwargs
    assert factory.call_args.args == ("s3",)
    assert kwargs["endpoint_url"] is None
    assert kwargs["aws_access_key_id"] is None
    assert kwargs["aws_secret_access_key"] is None
    assert kwargs["config"].s3 == {"addressing_style": "path"}
    assert kwargs["config"].signature_version == "s3v4"


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1", ""])
def test_missing_bucket_is_created_once_with_region_constraint(s3, region):
    build, client, _ = s3
    client.head_bucket.side_effect = storage_error("404")
    storage = build(object_storage_region=region)
    data = BytesIO(b"image")
    storage.put("first", data, content_type="application/octet-stream")
    storage.put("second", data)
    expected = {"Bucket": "unit-artifacts"}
    if region == "eu-west-1":
        expected["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket.assert_called_once_with(**expected)
    client.head_bucket.assert_called_once_with(Bucket="unit-artifacts")
    assert client.upload_fileobj.call_args_list[0].args == (data, "unit-artifacts", "first")
    assert client.upload_fileobj.call_args_list[0].kwargs == {"ExtraArgs": {"ContentType": "application/octet-stream"}}
    assert client.upload_fileobj.call_args_list[1].kwargs == {"ExtraArgs": None}


def test_existing_bucket_is_checked_once(s3):
    build, client, _ = s3
    storage = build()
    storage.put("one", BytesIO(b"1"))
    storage.put("two", BytesIO(b"2"))
    client.head_bucket.assert_called_once()
    client.create_bucket.assert_not_called()


def test_disabled_bucket_creation_does_not_probe_bucket(s3):
    build, client, _ = s3
    build(object_storage_auto_create_bucket=False).put("image", BytesIO(b"image"))
    client.head_bucket.assert_not_called()
    client.create_bucket.assert_not_called()
    client.upload_fileobj.assert_called_once()


@pytest.mark.parametrize("failure_stage", ["head_bucket", "create_bucket"])
def test_bucket_failure_propagates_and_next_upload_retries(s3, failure_stage):
    build, client, _ = s3
    failure = storage_error("AccessDenied")
    if failure_stage == "create_bucket":
        client.head_bucket.side_effect = storage_error("404")
    getattr(client, failure_stage).side_effect = failure
    storage = build()
    with pytest.raises(ClientError) as exc:
        storage.put("image", BytesIO(b"image"))
    assert exc.value is failure
    client.upload_fileobj.assert_not_called()
    getattr(client, failure_stage).side_effect = None
    storage.put("image", BytesIO(b"image"))
    assert client.head_bucket.call_count == 2
    client.upload_fileobj.assert_called_once()


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_missing_objects_translate_to_port_contract(s3, code):
    build, client, _ = s3
    storage = build()
    client.get_object.side_effect = storage_error(code)
    client.head_object.side_effect = storage_error(code)
    with pytest.raises(StoredObjectNotFoundError, match="missing"):
        storage.get("missing")
    assert storage.exists("missing") is False


@pytest.mark.parametrize(
    "operation,method", [("get", "get_object"), ("exists", "head_object"), ("delete", "delete_object")]
)
def test_storage_errors_are_not_hidden_as_missing_objects(s3, operation, method):
    build, client, _ = s3
    failure = storage_error("AccessDenied")
    getattr(client, method).side_effect = failure
    with pytest.raises(ClientError) as exc:
        getattr(build(), operation)("image")
    assert exc.value is failure


@pytest.mark.parametrize("ending", ["exhausted", "cancelled", "read_error"])
def test_download_stream_closes_body_on_every_exit(s3, ending):
    build, client, _ = s3
    body = Mock()

    def chunks():
        yield b"first"
        if ending == "read_error":
            raise OSError("connection lost")
        yield b"second"

    body.iter_chunks.return_value = chunks()
    client.get_object.return_value = {"Body": body, "ContentLength": "11", "ContentType": "application/octet-stream"}
    stored = build().get("image")
    assert stored.size == 11
    assert stored.content_type == "application/octet-stream"
    client.get_object.assert_called_once_with(Bucket="unit-artifacts", Key="image")
    body.close.assert_not_called()
    assert next(stored.chunks) == b"first"
    if ending == "cancelled":
        stored.chunks.close()
    elif ending == "read_error":
        with pytest.raises(OSError, match="connection lost"):
            list(stored.chunks)
    else:
        assert list(stored.chunks) == [b"second"]
    body.close.assert_called_once()


def test_exists_and_delete_use_configured_bucket(s3):
    build, client, _ = s3
    storage = build()
    assert storage.exists("image") is True
    storage.delete("image")
    client.head_object.assert_called_once_with(Bucket="unit-artifacts", Key="image")
    client.delete_object.assert_called_once_with(Bucket="unit-artifacts", Key="image")


def test_unknown_storage_provider_fails_before_client_creation(s3):
    _, _, factory = s3
    settings = Settings(_env_file=None).model_copy(update={"object_storage_provider": "unsupported"})
    with pytest.raises(RuntimeError, match="Unsupported object_storage.provider"):
        object_storage(settings)
    factory.assert_not_called()
