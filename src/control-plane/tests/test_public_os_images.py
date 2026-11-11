"""Public OS images for Meteor Imager: listed and downloaded without login, only when published."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.artifacts.dependencies import get_public_os_image_rate_limiter
from app.devices.rate_limit import InMemoryRateLimiter
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_artifacts import IMAGE, _device_type, _owner_setup, _upload

PUBLIC = "/api/v1/os-images"


def _publish(client: TestClient, org_id, headers, artifact_id: str, published: bool = True):
    return client.patch(
        f"/api/v1/organizations/{org_id}/artifacts/{artifact_id}", headers=headers, json={"published": published}
    )


def test_only_published_os_images_are_listed_and_downloadable_without_login(
    client: TestClient, db_session: Session
) -> None:
    org, headers = _owner_setup(client, db_session)
    device_type = _device_type(client, org.id, headers)
    artifact = _upload(client, org.id, headers, device_type_id=device_type["id"], description="Lite").json()
    assert artifact["published"] is False
    assert client.get(PUBLIC).json() == []
    assert client.get(f"{PUBLIC}/{artifact['id']}/download").status_code == 404

    published = _publish(client, org.id, headers, artifact["id"])
    assert published.status_code == 200, published.text
    assert published.json()["published"] is True

    [image] = client.get(PUBLIC).json()
    assert image["id"] == artifact["id"]
    assert image["device_type"] == "Raspberry Pi 4"
    assert image["description"] == "Lite"
    assert image["size_bytes"] == len(IMAGE)
    assert image["checksum_sha256"] == hashlib.sha256(IMAGE).hexdigest()
    assert "organization_id" not in image

    download = client.get(image["download_url"])
    assert download.status_code == 200
    assert download.content == IMAGE
    assert download.headers["x-checksum-sha256"] == image["checksum_sha256"]

    assert _publish(client, org.id, headers, artifact["id"], published=False).status_code == 200
    assert client.get(PUBLIC).json() == []
    assert client.get(image["download_url"]).status_code == 404


def test_published_can_be_set_at_upload_for_os_images_only(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    image = _upload(client, org.id, headers, published="true")
    assert image.status_code == 201, image.text
    assert image.json()["published"] is True

    firmware = _upload(client, org.id, headers, type="firmware", version="2.0", published="true")
    assert firmware.status_code == 422

    firmware = _upload(client, org.id, headers, type="firmware", version="2.0").json()
    response = _publish(client, org.id, headers, firmware["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "artifact_not_publishable"


def test_only_managers_can_publish(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    artifact = _upload(client, org.id, headers).json()
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    response = _publish(client, org.id, auth_header(client, "viewer@example.com"), artifact["id"])
    assert response.status_code == 403
    assert client.get(PUBLIC).json() == []


def test_developers_manage_artifacts_but_cannot_publish(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    artifact = _upload(client, org.id, headers).json()
    developer = create_user(db_session, email="dev-publish@example.com")
    add_member(db_session, org, developer, OrganizationRole.DEVELOPER)
    dev_headers = auth_header(client, "dev-publish@example.com")

    response = _publish(client, org.id, dev_headers, artifact["id"])
    assert response.status_code == 403
    response = _upload(client, org.id, dev_headers, version="2.0", published="true")
    assert response.status_code == 403
    assert client.get(PUBLIC).json() == []

    assert _upload(client, org.id, dev_headers, version="2.0").status_code == 201


def test_public_routes_are_rate_limited_per_ip(client: TestClient) -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    client.app.dependency_overrides[get_public_os_image_rate_limiter] = lambda: limiter
    assert client.get(PUBLIC).status_code == 200
    response = client.get(PUBLIC)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
