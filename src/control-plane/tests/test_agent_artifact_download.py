"""Agent artifact download: desired-release gate and operator download unchanged."""

from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.deployments.software_service import DeviceSoftwareService
from app.releases.repository import ReleaseRepository
from tests.conftest import auth_header, create_org_with_owner, create_user

UNIT = b"[Unit]\nDescription=Camera\n[Service]\nExecStart=/bin/true\n"


def _setup(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap"},
    ).json()["token"]
    artifact = client.post(
        f"/api/v1/organizations/{org.id}/artifacts",
        headers=headers,
        data={"name": "camera-agent", "version": "1.0", "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    ).json()
    release = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": "1.2.0",
            "kind": "systemd",
            "artifact_id": artifact["id"],
            "spec": {"unit_name": "camera-agent.service"},
        },
    ).json()
    return org, headers, token, artifact, release


def _register(client: TestClient, token: str, *, name: str, mac: str) -> dict:
    return client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": [mac], "name": name},
    ).json()


def test_assigned_device_can_download_desired_artifact(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, artifact, release = _setup(client, db_session)
    reg = _register(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")

    service = DeviceSoftwareService(db_session)
    release_row = ReleaseRepository(db_session).get(
        organization_id=org.id, release_id=uuid.UUID(release["id"])
    )
    assert release_row is not None
    service.set_desired_software(
        organization_id=org.id,
        device_id=uuid.UUID(reg["device_id"]),
        release=release_row,
    )

    device_headers = {"Authorization": f"Bearer {reg['device_token']}"}
    download = client.get(
        f"/api/v1/agent/artifacts/{artifact['id']}/download",
        headers=device_headers,
    )
    assert download.status_code == 200, download.text
    assert download.content == UNIT
    assert download.headers["x-checksum-sha256"] == artifact["checksum_sha256"]
    assert hashlib.sha256(download.content).hexdigest() == artifact["checksum_sha256"]

    # Operator download still works.
    operator = client.get(
        f"/api/v1/organizations/{org.id}/artifacts/{artifact['id']}/download",
        headers=headers,
    )
    assert operator.status_code == 200
    assert operator.content == UNIT


def test_other_device_cannot_download_same_org_artifact(
    client: TestClient, db_session: Session
) -> None:
    org, _headers, token, artifact, release = _setup(client, db_session)
    assigned = _register(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    other = _register(client, token, name="edge-02", mac="aa:bb:cc:dd:ee:02")

    service = DeviceSoftwareService(db_session)
    release_row = ReleaseRepository(db_session).get(
        organization_id=org.id, release_id=uuid.UUID(release["id"])
    )
    assert release_row is not None
    service.set_desired_software(
        organization_id=org.id,
        device_id=uuid.UUID(assigned["device_id"]),
        release=release_row,
    )

    denied = client.get(
        f"/api/v1/agent/artifacts/{artifact['id']}/download",
        headers={"Authorization": f"Bearer {other['device_token']}"},
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "artifact_not_found"


def test_download_without_desired_software_is_404(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, artifact, _release = _setup(client, db_session)
    reg = _register(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    response = client.get(
        f"/api/v1/agent/artifacts/{artifact['id']}/download",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
    )
    assert response.status_code == 404
