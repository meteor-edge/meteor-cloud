"""Artifact business rules exercised without a database or object-store server."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock

import pytest
from jose import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.artifacts.models import Artifact, ArtifactType
from app.artifacts.repository import ArtifactRepository
from app.artifacts.schemas import ArtifactCreate
from app.artifacts.service import ArtifactService, ArtifactTooLargeError, safe_file_name
from app.audit.service import AuditRecorder
from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, UnauthorizedError
from app.devices.repository import DeviceTypeRepository
from app.identity.models import User
from app.tenancy.models import OrganizationMembership, OrganizationRole
from app.tenancy.repository import OrganizationRepository
from tests.storage import InMemoryObjectStorage


@pytest.fixture
def service():
    settings = Settings(_env_file=None).model_copy(
        update={"artifact_max_upload_bytes": 10, "jwt_secret_key": "unit-test-signing-key", "jwt_algorithm": "HS256"}
    )
    service = ArtifactService(Mock(spec=Session), InMemoryObjectStorage(), settings)
    service.organizations = Mock(spec=OrganizationRepository)
    service.organizations.get_for_user.return_value = (Mock(), OrganizationMembership(role=OrganizationRole.OWNER))
    service.artifacts = Mock(spec=ArtifactRepository)
    service.artifacts.find_duplicate.return_value = None
    service.device_types = Mock(spec=DeviceTypeRepository)
    service.audit = Mock(spec=AuditRecorder)

    def refresh(artifact):
        artifact.created_at = artifact.updated_at = datetime.now(UTC)

    service.session.refresh.side_effect = refresh
    return service


@pytest.fixture
def upload_args():
    return {
        "actor": User(id=uuid.uuid4()),
        "organization_id": uuid.uuid4(),
        "payload": ArtifactCreate(name="OS", version="1.0", metadata={"channel": "stable"}),
        "data": BytesIO(b"0123456789"),
        "file_name": "../../disk image.img",
        "content_type": "application/octet-stream",
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "artifact.bin"),
        ("", "artifact.bin"),
        ("../..", "artifact.bin"),
        (r"C:\images\disk image.img", "disk-image.img"),
        ("../../a\r\nb.img", "a-b.img"),
        (".hidden.img", "hidden.img"),
        ("a" * 201, "a" * 200),
    ],
)
def test_file_names_cannot_escape_the_artifact_key(raw, expected):
    assert safe_file_name(raw) == expected


def test_upload_accepts_exact_limit_and_hashes_from_current_stream_position(service, upload_args):
    upload_args["data"] = BytesIO(b"prefix0123456789")
    upload_args["data"].seek(6)
    result = service.upload_artifact(**upload_args, declared_size=10)
    key = f"organizations/{upload_args['organization_id']}/artifacts/{result.id}/disk-image.img"
    assert service.storage.objects == {key: (b"0123456789", "application/octet-stream")}
    assert result.size_bytes == 10
    assert result.checksum_sha256 == hashlib.sha256(b"0123456789").hexdigest()
    assert result.metadata == {"channel": "stable"}
    assert "storage_key" not in result.model_dump()
    service.session.commit.assert_called_once()
    assert service.audit.record.call_args.kwargs["action"] == "artifact.upload"


def test_declared_oversize_is_rejected_without_reading_or_writing(service, upload_args):
    upload_args["data"] = Mock()
    with pytest.raises(ArtifactTooLargeError):
        service.upload_artifact(**upload_args, declared_size=11)
    upload_args["data"].read.assert_not_called()
    assert service.storage.objects == {}
    service.artifacts.create.assert_not_called()


@pytest.mark.parametrize("wrapped", [False, True])
def test_actual_size_overrides_underreported_size_even_if_adapter_wraps_error(service, upload_args, wrapped):
    upload_args["data"] = BytesIO(b"01234567890")
    storage = service.storage

    def partial_put(key, data, **kwargs):
        storage.objects[key] = (data.read(5), None)
        try:
            data.read()
        except ArtifactTooLargeError as exc:
            if wrapped:
                raise RuntimeError("multipart upload failed") from exc
            raise

    storage.put = partial_put
    with pytest.raises(ArtifactTooLargeError) as exc:
        service.upload_artifact(**upload_args, declared_size=1)
    assert exc.value.status_code == 413
    assert storage.objects == {}
    service.artifacts.create.assert_not_called()
    service.session.commit.assert_not_called()


def test_empty_upload_removes_object_and_does_not_commit(service, upload_args):
    upload_args["data"] = BytesIO()
    with pytest.raises(AppError) as exc:
        service.upload_artifact(**upload_args)
    assert (exc.value.code, exc.value.status_code) == ("artifact_empty", 422)
    assert service.storage.objects == {}
    service.session.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["create", "audit", "commit"])
@pytest.mark.parametrize("integrity_error", [False, True])
def test_metadata_failure_rolls_back_and_removes_uploaded_content(service, upload_args, stage, integrity_error):
    failure = IntegrityError("insert", {}, ValueError("duplicate")) if integrity_error else RuntimeError("db offline")
    operation = {"create": service.artifacts.create, "audit": service.audit.record, "commit": service.session.commit}[
        stage
    ]
    operation.side_effect = failure
    with pytest.raises(ConflictError if integrity_error else RuntimeError) as exc:
        service.upload_artifact(**upload_args)
    if integrity_error:
        assert exc.value.code == "artifact_exists"
    else:
        assert exc.value is failure
    service.session.rollback.assert_called_once()
    service.session.refresh.assert_not_called()
    assert service.storage.objects == {}


def test_storage_failure_and_cleanup_failure_preserve_original_api_error(service, upload_args):
    service.storage.put = Mock(side_effect=OSError("offline"))
    service.storage.delete = Mock(side_effect=OSError("still offline"))
    with pytest.raises(AppError) as exc:
        service.upload_artifact(**upload_args)
    assert (exc.value.code, exc.value.status_code) == ("storage_unavailable", 503)
    service.storage.delete.assert_called_once()
    service.artifacts.create.assert_not_called()
    service.session.commit.assert_not_called()


@pytest.mark.parametrize("role", [OrganizationRole.VIEWER, OrganizationRole.MEMBER])
def test_non_managers_cannot_start_upload(service, upload_args, role):
    service.organizations.get_for_user.return_value = (Mock(), OrganizationMembership(role=role))
    with pytest.raises(AppError) as exc:
        service.upload_artifact(**upload_args)
    assert exc.value.status_code == 403
    assert service.storage.objects == {}
    service.artifacts.create.assert_not_called()


@pytest.fixture
def download_args(service, upload_args):
    artifact = Artifact(
        id=uuid.uuid4(),
        organization_id=upload_args["organization_id"],
        storage_key="image",
        name="OS",
        version="1.0",
        type=ArtifactType.OS_IMAGE,
    )
    service.artifacts.get.return_value = artifact
    service.storage.put("image", BytesIO(b"content"))
    return {"actor": upload_args["actor"], "organization_id": artifact.organization_id, "artifact_id": artifact.id}


def test_download_ticket_has_scoped_claims_and_configured_expiry(service, download_args):
    before = datetime.now(UTC)
    link = service.create_download_link(**download_args)
    after = datetime.now(UTC)
    claims = jwt.decode(link.ticket, service.settings.jwt_secret_key, algorithms=[service.settings.jwt_algorithm])
    assert claims["typ"] == "artifact_download"
    assert claims["uid"] == str(download_args["actor"].id)
    assert claims["oid"] == str(download_args["organization_id"])
    assert claims["aid"] == str(download_args["artifact_id"])
    assert "sub" not in claims
    ttl = timedelta(seconds=service.settings.artifact_download_link_ttl_seconds)
    assert before + ttl <= link.expires_at <= after + ttl
    assert claims["exp"] == int(link.expires_at.timestamp())
    assert link.url == f"/api/v1/organizations/{claims['oid']}/artifacts/{claims['aid']}/download"
    _, stored = service.open_download_with_ticket(
        organization_id=download_args["organization_id"],
        artifact_id=download_args["artifact_id"],
        ticket=link.ticket,
    )
    assert b"".join(stored.chunks) == b"content"


@pytest.mark.parametrize(
    "mutation", ["expired", "wrong_type", "wrong_org", "wrong_artifact", "bad_user", "missing_user"]
)
def test_signed_but_invalid_tickets_never_open_storage(service, download_args, mutation):
    link = service.create_download_link(**download_args)
    claims = jwt.get_unverified_claims(link.ticket)
    changes = {
        "expired": ("exp", 1),
        "wrong_type": ("typ", "access"),
        "wrong_org": ("oid", str(uuid.uuid4())),
        "wrong_artifact": ("aid", str(uuid.uuid4())),
        "bad_user": ("uid", "invalid-uuid"),
        "missing_user": ("uid", None),
    }
    key, value = changes[mutation]
    claims[key] = value
    if mutation == "missing_user":
        del claims[key]
    ticket = jwt.encode(claims, service.settings.jwt_secret_key, algorithm=service.settings.jwt_algorithm)
    service.storage.get = Mock()
    with pytest.raises(UnauthorizedError) as exc:
        service.open_download_with_ticket(
            organization_id=download_args["organization_id"],
            artifact_id=download_args["artifact_id"],
            ticket=ticket,
        )
    assert exc.value.code == "invalid_download_link"
    service.storage.get.assert_not_called()


def test_previously_issued_ticket_is_revoked_when_membership_is_removed(service, download_args):
    link = service.create_download_link(**download_args)
    service.organizations.get_for_user.return_value = None
    service.storage.get = Mock()
    with pytest.raises(UnauthorizedError):
        service.open_download_with_ticket(
            organization_id=download_args["organization_id"],
            artifact_id=download_args["artifact_id"],
            ticket=link.ticket,
        )
    service.organizations.get_for_user.assert_called_with(
        organization_id=download_args["organization_id"],
        user_id=download_args["actor"].id,
    )
    service.storage.get.assert_not_called()


@pytest.mark.parametrize("offline", [False, True])
def test_download_distinguishes_missing_content_from_storage_outage(service, download_args, offline):
    service.storage.objects.clear()
    if offline:
        service.storage.get = Mock(side_effect=OSError("offline"))
    with pytest.raises(AppError) as exc:
        service.open_download(**download_args)
    assert (exc.value.code, exc.value.status_code) == (
        ("storage_unavailable", 503) if offline else ("artifact_content_missing", 404)
    )


def test_delete_preserves_content_when_metadata_commit_fails(service, download_args):
    service.session.commit.side_effect = RuntimeError("db offline")
    with pytest.raises(RuntimeError, match="db offline"):
        service.delete_artifact(**download_args)
    assert service.storage.exists("image")


def test_delete_commits_metadata_before_best_effort_content_cleanup(service, download_args):
    def delete(key):
        service.session.commit.assert_called_once()
        service.artifacts.delete.assert_called_once_with(service.artifacts.get.return_value)
        raise OSError("offline")

    service.storage.delete = Mock(side_effect=delete)
    service.delete_artifact(**download_args)
    service.storage.delete.assert_called_once_with("image")
    assert service.audit.record.call_args.kwargs["action"] == "artifact.delete"
