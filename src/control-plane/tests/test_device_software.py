"""Tests for desired vs installed device software (Phase 2)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.deployments.software_service import DeviceSoftwareService
from app.releases.models import Release
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
    registration = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"], "name": "edge-01"},
    ).json()
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
    return org, headers, registration, release


def test_set_desired_lists_pending_until_agent_reports(client: TestClient, db_session: Session) -> None:
    org, headers, registration, release = _setup(client, db_session)
    device_id = registration["device_id"]

    service = DeviceSoftwareService(db_session)
    release_row = ReleaseRepository(db_session).get(
        organization_id=org.id, release_id=uuid.UUID(release["id"])
    )
    assert release_row is not None
    service.set_desired_software(
        organization_id=org.id,
        device_id=uuid.UUID(device_id),
        release=release_row,
    )

    listed = client.get(
        f"/api/v1/organizations/{org.id}/devices/{device_id}/software",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()
    assert len(items) == 1
    assert items[0]["workload_key"] == "camera-agent.service"
    assert items[0]["desired_release"]["version"] == "1.2.0"
    assert items[0]["installed_release_id"] is None
    assert items[0]["sync_status"] == "pending"

    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    desired = client.get("/api/v1/agent/software", headers=device_headers)
    assert desired.status_code == 200
    assert len(desired.json()) == 1
    assert desired.json()[0]["release_version"] == "1.2.0"
    assert desired.json()[0]["artifact_id"] == release["artifact_id"]


def test_heartbeat_report_marks_in_sync(client: TestClient, db_session: Session) -> None:
    org, headers, registration, release = _setup(client, db_session)
    device_id = registration["device_id"]
    service = DeviceSoftwareService(db_session)
    release_row = db_session.get(Release, uuid.UUID(release["id"]))
    assert release_row is not None
    service.set_desired_software(
        organization_id=org.id,
        device_id=uuid.UUID(device_id),
        release=release_row,
    )

    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    heartbeat = client.post(
        "/api/v1/agent/heartbeat",
        headers=device_headers,
        json={
            "capabilities": {"systemd": True},
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release["id"],
                    "installed_version": "1.2.0",
                    "health": "healthy",
                }
            ],
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    body = heartbeat.json()
    assert body["status"] == "online"
    assert len(body["desired_software"]) == 1

    listed = client.get(
        f"/api/v1/organizations/{org.id}/devices/{device_id}/software",
        headers=headers,
    ).json()
    assert listed[0]["sync_status"] == "in_sync"
    assert listed[0]["health"] == "healthy"
    assert listed[0]["installed_release"]["version"] == "1.2.0"
    assert listed[0]["progress_percent"] == 100


def test_unknown_workload_report_ignored(client: TestClient, db_session: Session) -> None:
    org, headers, registration, release = _setup(client, db_session)
    device_id = registration["device_id"]
    service = DeviceSoftwareService(db_session)
    release_row = db_session.get(Release, uuid.UUID(release["id"]))
    assert release_row is not None
    service.set_desired_software(
        organization_id=org.id,
        device_id=uuid.UUID(device_id),
        release=release_row,
    )

    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    client.post(
        "/api/v1/agent/heartbeat",
        headers=device_headers,
        json={
            "software_report": [
                {
                    "workload_key": "unknown.service",
                    "installed_release_id": release["id"],
                    "health": "healthy",
                }
            ],
        },
    )

    listed = client.get(
        f"/api/v1/organizations/{org.id}/devices/{device_id}/software",
        headers=headers,
    ).json()
    assert len(listed) == 1
    assert listed[0]["sync_status"] == "pending"
    assert listed[0]["installed_release_id"] is None


def test_empty_software_list_for_new_device(client: TestClient, db_session: Session) -> None:
    org, headers, registration, _release = _setup(client, db_session)
    listed = client.get(
        f"/api/v1/organizations/{org.id}/devices/{registration['device_id']}/software",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json() == []
