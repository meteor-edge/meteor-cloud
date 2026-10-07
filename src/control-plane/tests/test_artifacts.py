"""Tests for artifacts (OS images, firmware, …) and their object-storage content."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user
from tests.storage import InMemoryObjectStorage

IMAGE = b"meteor-os-image-bytes" * 100


def _device_type(client: TestClient, org_id, headers, name: str = "Raspberry Pi 4") -> dict:
    response = client.post(
        f"/api/v1/organizations/{org_id}/device-types",
        headers=headers,
        json={"name": name, "manufacturer": "Raspberry Pi Ltd", "architecture": "arm64"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client: TestClient, org_id, headers, *, content: bytes = IMAGE, **fields):
    data = {"name": "MeteorCloud OS", "version": "1.1", "type": "os_image", **fields}
    return client.post(
        f"/api/v1/organizations/{org_id}/artifacts",
        headers=headers,
        data=data,
        files={"file": ("meteor os 1.1.img", content, "application/octet-stream")},
    )


def _owner_setup(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, "owner@example.com")


def test_upload_list_download_and_delete_os_image(
    client: TestClient, db_session: Session, object_storage: InMemoryObjectStorage
) -> None:
    org, headers = _owner_setup(client, db_session)
    device_type = _device_type(client, org.id, headers)

    uploaded = _upload(client, org.id, headers, device_type_id=device_type["id"], description="Stable")
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()
    assert artifact["type"] == "os_image"
    assert artifact["device_type_id"] == device_type["id"]
    assert artifact["size_bytes"] == len(IMAGE)
    assert artifact["checksum_sha256"] == hashlib.sha256(IMAGE).hexdigest()
    assert artifact["file_name"] == "meteor-os-1.1.img"
    assert "storage_key" not in artifact

    # Only metadata is in PostgreSQL; the bytes are in object storage under an org-scoped key.
    [key] = object_storage.objects
    assert key == f"organizations/{org.id}/artifacts/{artifact['id']}/meteor-os-1.1.img"
    assert object_storage.objects[key][0] == IMAGE

    listed = client.get(
        f"/api/v1/organizations/{org.id}/artifacts?device_type_id={device_type['id']}&type=os_image",
        headers=headers,
    ).json()
    assert listed["total"] == 1
    assert client.get(f"/api/v1/organizations/{org.id}/artifacts?type=firmware", headers=headers).json()["total"] == 0

    type_detail = client.get(f"/api/v1/organizations/{org.id}/device-types/{device_type['id']}", headers=headers)
    assert type_detail.json()["artifact_count"] == 1

    download = client.get(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == IMAGE
    assert download.headers["x-checksum-sha256"] == artifact["checksum_sha256"]
    assert "attachment" in download.headers["content-disposition"]

    deleted = client.delete(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}", headers=headers)
    assert deleted.status_code == 204
    assert object_storage.objects == {}
    missing = client.get(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}", headers=headers)
    assert missing.status_code == 404


def test_duplicate_version_rejected_but_other_device_type_allowed(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    rpi4 = _device_type(client, org.id, headers, "Raspberry Pi 4")
    rpi5 = _device_type(client, org.id, headers, "Raspberry Pi 5")

    assert _upload(client, org.id, headers, device_type_id=rpi4["id"]).status_code == 201
    duplicate = _upload(client, org.id, headers, device_type_id=rpi4["id"])
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "artifact_exists"

    assert _upload(client, org.id, headers, device_type_id=rpi5["id"]).status_code == 201
    assert _upload(client, org.id, headers, device_type_id=rpi4["id"], version="1.2").status_code == 201

    # Organization-wide artifacts (no device type) are unique as well.
    assert _upload(client, org.id, headers, type="configuration").status_code == 201
    assert _upload(client, org.id, headers, type="configuration").status_code == 409


def test_upload_validation(client: TestClient, db_session: Session, object_storage: InMemoryObjectStorage) -> None:
    org, headers = _owner_setup(client, db_session)

    bad_version = _upload(client, org.id, headers, version="1.0 beta/../")
    assert bad_version.status_code == 422
    assert bad_version.json()["error"]["code"] == "validation_error"

    bad_type = _upload(client, org.id, headers, type="docker_image")
    assert bad_type.status_code == 422

    bad_metadata = _upload(client, org.id, headers, metadata="not json")
    assert bad_metadata.status_code == 422

    empty = _upload(client, org.id, headers, content=b"")
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "artifact_empty"
    assert object_storage.objects == {}


def test_upload_rejects_files_over_the_size_limit(
    client: TestClient, db_session: Session, object_storage: InMemoryObjectStorage
) -> None:
    org, headers = _owner_setup(client, db_session)
    small_limit = get_settings().model_copy(update={"artifact_max_upload_bytes": 10})
    client.app.dependency_overrides[get_settings] = lambda: small_limit

    response = _upload(client, org.id, headers, content=b"x" * 11)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "artifact_too_large"
    assert object_storage.objects == {}


def test_storage_failure_does_not_create_artifact(
    client: TestClient, db_session: Session, object_storage: InMemoryObjectStorage
) -> None:
    org, headers = _owner_setup(client, db_session)
    object_storage.fail_puts = True

    response = _upload(client, org.id, headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert client.get(f"/api/v1/organizations/{org.id}/artifacts", headers=headers).json()["total"] == 0


def test_viewer_can_read_but_not_manage_artifacts(client: TestClient, db_session: Session) -> None:
    org, owner_headers = _owner_setup(client, db_session)
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    artifact = _upload(client, org.id, owner_headers).json()
    viewer_headers = auth_header(client, "viewer@example.com")

    assert client.get(f"/api/v1/organizations/{org.id}/artifacts", headers=viewer_headers).json()["total"] == 1
    download = client.get(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}/download", headers=viewer_headers)
    assert download.status_code == 200

    upload = _upload(client, org.id, viewer_headers, version="2.0")
    assert upload.status_code == 403
    delete = client.delete(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}", headers=viewer_headers)
    assert delete.status_code == 403


def test_artifacts_are_isolated_per_organization(client: TestClient, db_session: Session) -> None:
    org, owner_headers = _owner_setup(client, db_session)
    stranger = create_user(db_session, email="stranger@example.com")
    other_org, _ = create_org_with_owner(db_session, stranger, slug="stranger-org")
    stranger_headers = auth_header(client, "stranger@example.com")
    artifact = _upload(client, org.id, owner_headers).json()

    # Not a member of the owning organization.
    response = client.get(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}", headers=stranger_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "organization_not_found"

    # Addressing the artifact through the stranger's own organization does not find it.
    response = client.get(
        f"/api/v1/organizations/{other_org.id}/artifacts/{artifact['id']}/download", headers=stranger_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"

    # A device type from another organization cannot be referenced.
    foreign_type = _device_type(client, other_org.id, stranger_headers)
    response = _upload(client, org.id, owner_headers, version="9.9", device_type_id=foreign_type["id"])
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "device_type_not_found"


def test_download_link_works_without_bearer_and_is_scoped(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    first = _upload(client, org.id, headers).json()
    second = _upload(client, org.id, headers, version="1.2").json()

    link = client.post(f"/api/v1/organizations/{org.id}/artifacts/{first['id']}/download-link", headers=headers).json()
    assert client.get(link["url"]).content == IMAGE

    ticket = link["url"].rsplit("/", 1)[-1]
    # The ticket only opens the artifact it was issued for…
    other = client.get(f"/api/v1/organizations/{org.id}/artifacts/{second['id']}/download/{ticket}")
    assert other.status_code == 401
    assert other.json()["error"]["code"] == "invalid_download_link"
    # …is rejected when tampered with…
    assert client.get(f"{link['url']}x").status_code == 401
    # …and is never accepted as a user access token.
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ticket}"})
    assert me.status_code == 401


def test_device_type_with_artifacts_cannot_be_deleted(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    device_type = _device_type(client, org.id, headers)
    artifact = _upload(client, org.id, headers, device_type_id=device_type["id"]).json()

    blocked = client.delete(f"/api/v1/organizations/{org.id}/device-types/{device_type['id']}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "device_type_has_artifacts"

    client.delete(f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}", headers=headers)
    allowed = client.delete(f"/api/v1/organizations/{org.id}/device-types/{device_type['id']}", headers=headers)
    assert allowed.status_code == 204
