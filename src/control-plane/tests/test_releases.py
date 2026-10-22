"""Tests for the software release catalog."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user

UNIT = b"[Unit]\nDescription=Camera\n[Service]\nExecStart=/usr/bin/camera\n"


def _owner_setup(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, "owner@example.com")


def _upload_systemd(client: TestClient, org_id, headers, *, name: str = "camera-agent", version: str = "1.0"):
    return client.post(
        f"/api/v1/organizations/{org_id}/artifacts",
        headers=headers,
        data={"name": name, "version": version, "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    )


def _create_systemd_release(client: TestClient, org_id, headers, *, artifact_id: str, **fields):
    body = {
        "name": "camera-agent",
        "version": "1.2.0",
        "kind": "systemd",
        "artifact_id": artifact_id,
        "spec": {"unit_name": "camera-agent.service"},
        **fields,
    }
    return client.post(f"/api/v1/organizations/{org_id}/releases", headers=headers, json=body)


def test_create_list_get_update_delete_systemd_release(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    artifact = _upload_systemd(client, org.id, headers).json()

    created = _create_systemd_release(client, org.id, headers, artifact_id=artifact["id"], description="Stable")
    assert created.status_code == 201, created.text
    release = created.json()
    assert release["kind"] == "systemd"
    assert release["name"] == "camera-agent"
    assert release["version"] == "1.2.0"
    assert release["artifact_id"] == artifact["id"]
    assert release["spec"]["unit_name"] == "camera-agent.service"
    assert release["spec"]["start"] is True
    assert release["description"] == "Stable"

    listed = client.get(f"/api/v1/organizations/{org.id}/releases?kind=systemd", headers=headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == release["id"]

    detail = client.get(f"/api/v1/organizations/{org.id}/releases/{release['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == release["id"]

    updated = client.patch(
        f"/api/v1/organizations/{org.id}/releases/{release['id']}",
        headers=headers,
        json={"description": "Updated", "spec": {"unit_name": "camera-agent.service", "start": False}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "Updated"
    assert updated.json()["spec"]["start"] is False

    deleted = client.delete(f"/api/v1/organizations/{org.id}/releases/{release['id']}", headers=headers)
    assert deleted.status_code == 204
    missing = client.get(f"/api/v1/organizations/{org.id}/releases/{release['id']}", headers=headers)
    assert missing.status_code == 404


def test_wrong_artifact_type_rejected(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    os_image = client.post(
        f"/api/v1/organizations/{org.id}/artifacts",
        headers=headers,
        data={"name": "os", "version": "1", "type": "os_image"},
        files={"file": ("os.img", b"image-bytes", "application/octet-stream")},
    ).json()

    response = _create_systemd_release(client, org.id, headers, artifact_id=os_image["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "artifact_type_mismatch"


def test_delete_release_in_use_by_deployment_returns_409(
    client: TestClient, db_session: Session
) -> None:
    from tests.test_deployments import _register_device, _setup_release

    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-keep", mac="aa:bb:cc:30:00:01")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [reg["device_id"]]},
            "rollout": {"strategy": "immediate"},
        },
    )
    assert created.status_code == 201, created.text

    blocked = client.delete(
        f"/api/v1/organizations/{org.id}/releases/{release['id']}",
        headers=headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "release_in_use"

    artifact_id = release["artifact_id"]
    blocked_art = client.delete(
        f"/api/v1/organizations/{org.id}/artifacts/{artifact_id}",
        headers=headers,
    )
    assert blocked_art.status_code == 409
    assert blocked_art.json()["error"]["code"] == "artifact_in_use"


def test_duplicate_release_rejected(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    artifact = _upload_systemd(client, org.id, headers).json()

    assert _create_systemd_release(client, org.id, headers, artifact_id=artifact["id"]).status_code == 201
    duplicate = _create_systemd_release(client, org.id, headers, artifact_id=artifact["id"])
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "release_exists"

    case_dup = _create_systemd_release(
        client,
        org.id,
        headers,
        artifact_id=artifact["id"],
        name="Camera-Agent",
        version="1.2.0",
    )
    assert case_dup.status_code == 409


def test_missing_unit_name_rejected(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    artifact = _upload_systemd(client, org.id, headers).json()
    response = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": "1.0.0",
            "kind": "systemd",
            "artifact_id": artifact["id"],
            "spec": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_release_spec"


def test_docker_release_with_image_only(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "nginx",
            "version": "1.25",
            "kind": "docker",
            "spec": {"container_name": "nginx", "image": "nginx:1.25"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["artifact_id"] is None
    assert response.json()["spec"]["image"] == "nginx:1.25"


def test_docker_release_requires_image_or_artifact(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "nginx",
            "version": "1.25",
            "kind": "docker",
            "spec": {"container_name": "nginx"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_release_spec"


def test_viewer_can_read_but_not_create_releases(client: TestClient, db_session: Session) -> None:
    org, headers = _owner_setup(client, db_session)
    artifact = _upload_systemd(client, org.id, headers).json()
    created = _create_systemd_release(client, org.id, headers, artifact_id=artifact["id"])
    assert created.status_code == 201

    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    viewer_headers = auth_header(client, "viewer@example.com")

    listed = client.get(f"/api/v1/organizations/{org.id}/releases", headers=viewer_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    denied = _create_systemd_release(
        client,
        org.id,
        viewer_headers,
        artifact_id=artifact["id"],
        name="other",
        version="9.0.0",
    )
    assert denied.status_code == 403


def test_developer_can_create_releases(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    owner_headers = auth_header(client, "owner@example.com")
    artifact = _upload_systemd(client, org.id, owner_headers).json()

    developer = create_user(db_session, email="dev@example.com")
    add_member(db_session, org, developer, OrganizationRole.DEVELOPER)
    dev_headers = auth_header(client, "dev@example.com")

    created = _create_systemd_release(client, org.id, dev_headers, artifact_id=artifact["id"])
    assert created.status_code == 201, created.text


def test_operator_cannot_create_releases(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    owner_headers = auth_header(client, "owner@example.com")
    artifact = _upload_systemd(client, org.id, owner_headers).json()

    operator = create_user(db_session, email="ops@example.com")
    add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    ops_headers = auth_header(client, "ops@example.com")

    denied = _create_systemd_release(client, org.id, ops_headers, artifact_id=artifact["id"])
    assert denied.status_code == 403
