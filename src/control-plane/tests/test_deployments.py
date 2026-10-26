"""Tests for Deploy APIs (Phase 3): targeting, RBAC, cancel, progress."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.authorization.service import AuthzService
from app.core.config import get_settings
from app.devices.models import DeviceGroup
from app.mqtt.runtime import set_mqtt_publisher
from app.mqtt.service import NoopPublisher
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user
from tests.test_mqtt_commands import RecordingPublisher

UNIT = b"[Unit]\nDescription=Camera\n[Service]\nExecStart=/bin/true\n"


def _setup_release(client: TestClient, db_session: Session):
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
    return org, headers, token, release


def _register_device(client: TestClient, token: str, *, name: str, mac: str) -> dict:
    return client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": [mac], "name": name},
    ).json()


def test_deploy_to_group_sets_desired_and_progress(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    group = client.post(
        f"/api/v1/organizations/{org.id}/device-groups",
        headers=headers,
        json={"name": "Plant A", "slug": "plant-a"},
    ).json()

    device_ids = []
    for index in range(3):
        reg = _register_device(
            client, token, name=f"edge-{index}", mac=f"aa:bb:cc:dd:ee:0{index}"
        )
        device_ids.append(reg["device_id"])
        patched = client.patch(
            f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}",
            headers=headers,
            json={"device_group_id": group["id"]},
        )
        assert patched.status_code == 200, patched.text

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "name": "Rollout plant-a",
            "target": {"group_ids": [group["id"]]},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "in_progress"
    assert body["counts"]["total"] == 3
    assert body["counts"]["pending"] == 3
    assert len(body["devices"]) == 3
    assert body["release"]["version"] == "1.2.0"

    for device_id in device_ids:
        software = client.get(
            f"/api/v1/organizations/{org.id}/devices/{device_id}/software",
            headers=headers,
        ).json()
        assert len(software) == 1
        assert software[0]["desired_release"]["id"] == release["id"]
        assert software[0]["sync_status"] == "pending"

    listed = client.get(f"/api/v1/organizations/{org.id}/deployments", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_agent_report_marks_deployment_succeeded(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    device_id = reg["device_id"]

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [device_id]}},
    ).json()
    deployment_id = created["id"]

    heartbeat = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release["id"],
                    "installed_version": release["version"],
                    "health": "healthy",
                    "sync_status": "in_sync",
                    "progress_percent": 100,
                }
            ]
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text

    detail = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{deployment_id}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["counts"]["succeeded"] == 1
    assert body["status"] == "completed"
    assert body["devices"][0]["status"] == "succeeded"


def test_cancel_restores_previous_desired(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    artifact2 = client.post(
        f"/api/v1/organizations/{org.id}/artifacts",
        headers=headers,
        data={"name": "camera-agent", "version": "2.0", "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    ).json()
    release2 = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": "2.0.0",
            "kind": "systemd",
            "artifact_id": artifact2["id"],
            "spec": {"unit_name": "camera-agent.service"},
        },
    ).json()

    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    device_id = reg["device_id"]

    first = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [device_id]}},
    ).json()
    client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release["id"],
                    "health": "healthy",
                    "sync_status": "in_sync",
                }
            ]
        },
    )
    assert (
        client.get(
            f"/api/v1/organizations/{org.id}/deployments/{first['id']}", headers=headers
        ).json()["status"]
        == "completed"
    )

    second = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release2["id"], "target": {"device_ids": [device_id]}},
    ).json()
    cancelled = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{second['id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["devices"][0]["status"] == "cancelled"

    software = client.get(
        f"/api/v1/organizations/{org.id}/devices/{device_id}/software",
        headers=headers,
    ).json()
    assert software[0]["desired_release"]["id"] == release["id"]


def test_viewer_cannot_deploy(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")

    viewer_headers = auth_header(client, "viewer@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=viewer_headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    )
    assert created.status_code == 403

    # Seed a deployment as owner, then viewer can read.
    owned = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    ).json()
    listed = client.get(f"/api/v1/organizations/{org.id}/deployments", headers=viewer_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    cancel = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{owned['id']}/cancel",
        headers=viewer_headers,
    )
    assert cancel.status_code == 403


def test_scoped_operator_cannot_target_out_of_scope_device(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    operator = create_user(db_session, email="ops@example.com")
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)

    berlin = DeviceGroup(organization_id=org.id, name="Berlin", slug="berlin", labels={}, metadata_={})
    munich = DeviceGroup(organization_id=org.id, name="Munich", slug="munich", labels={}, metadata_={})
    db_session.add_all([berlin, munich])
    db_session.flush()
    AuthzService(db_session).replace_device_group_scope(membership, [berlin.id])
    db_session.commit()

    berlin_reg = _register_device(client, token, name="berlin-edge", mac="aa:bb:cc:dd:ee:01")
    munich_reg = _register_device(client, token, name="munich-edge", mac="aa:bb:cc:dd:ee:02")
    client.patch(
        f"/api/v1/organizations/{org.id}/devices/{berlin_reg['device_id']}",
        headers=headers,
        json={"device_group_id": str(berlin.id)},
    )
    client.patch(
        f"/api/v1/organizations/{org.id}/devices/{munich_reg['device_id']}",
        headers=headers,
        json={"device_group_id": str(munich.id)},
    )

    ops_headers = auth_header(client, "ops@example.com")
    denied = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=ops_headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [munich_reg["device_id"]]},
        },
    )
    assert denied.status_code == 404

    allowed = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=ops_headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [berlin_reg["device_id"]]},
        },
    )
    assert allowed.status_code == 201, allowed.text


def test_disabled_device_skipped_from_targets(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    enabled = _register_device(client, token, name="on", mac="aa:bb:cc:dd:ee:01")
    disabled = _register_device(client, token, name="off", mac="aa:bb:cc:dd:ee:02")
    client.post(
        f"/api/v1/organizations/{org.id}/devices/{disabled['device_id']}/disable",
        headers=headers,
    )

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [enabled["device_id"], disabled["device_id"]]},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["counts"]["total"] == 1
    assert created.json()["devices"][0]["device_id"] == enabled["device_id"]


def test_deploy_publishes_sync_desired_when_mqtt_enabled(
    client: TestClient, db_session: Session
) -> None:
    publisher = RecordingPublisher()
    set_mqtt_publisher(publisher)
    try:
        org, headers, token, release = _setup_release(client, db_session)
        reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
        created = client.post(
            f"/api/v1/organizations/{org.id}/deployments",
            headers=headers,
            json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
        )
        assert created.status_code == 201, created.text
        assert len(publisher.published) == 1
        topic, payload = publisher.published[0]
        assert topic == f"devices/{reg['device_id']}/commands"
        data = json.loads(payload)
        assert data["type"] == "sync_desired"
        assert isinstance(data["revision"], int)
    finally:
        set_mqtt_publisher(NoopPublisher())


def test_deployments_disabled_returns_404(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    disabled = get_settings().model_copy(update={"deployment_provider": "none"})
    client.app.dependency_overrides[get_settings] = lambda: disabled
    try:
        response = client.post(
            f"/api/v1/organizations/{org.id}/deployments",
            headers=headers,
            json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "deployments_disabled"
    finally:
        client.app.dependency_overrides.pop(get_settings, None)


def test_empty_target_rejected(client: TestClient, db_session: Session) -> None:
    org, headers, _token, release = _setup_release(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {}},
    )
    assert response.status_code == 422


def test_mender_provider_returns_501_on_deploy(client: TestClient, db_session: Session) -> None:
    """Releases stay available; execute path is stubbed until a Mender adapter exists."""
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    mender = get_settings().model_copy(update={"deployment_provider": "mender"})
    client.app.dependency_overrides[get_settings] = lambda: mender
    try:
        # Release catalog is independent of the execution provider.
        listed = client.get(f"/api/v1/organizations/{org.id}/releases", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1

        response = client.post(
            f"/api/v1/organizations/{org.id}/deployments",
            headers=headers,
            json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
        )
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "mender_not_implemented"
    finally:
        client.app.dependency_overrides.pop(get_settings, None)


def test_deploy_records_provider_native(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["provider"] == "native"
    assert body["provider_ref"] == body["id"]


def _report(client: TestClient, reg: dict, release: dict, *, health: str, last_error: str | None = None) -> None:
    item = {
        "workload_key": "camera-agent.service",
        "installed_release_id": release["id"],
        "installed_version": release["version"],
        "health": health,
        "sync_status": "in_sync",
        "progress_percent": 100,
        "last_error": last_error,
    }
    response = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={"software_report": [item]},
    )
    assert response.status_code == 200, response.text


def test_deploy_detail_shows_runtime_health(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-health", mac="aa:bb:cc:dd:ee:41")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    ).json()
    detail_url = f"/api/v1/organizations/{org.id}/deployments/{created['id']}"

    _report(client, reg, release, health="healthy")
    row = client.get(detail_url, headers=headers).json()["devices"][0]
    assert row["status"] == "succeeded"
    assert row["health"] == "healthy"
    assert row["reported_at"] is not None
    assert row["endpoint_availability"] is None  # this Deploy created no URL

    # The unit dies after a good install: install stays succeeded, health says unhealthy.
    _report(client, reg, release, health="unhealthy", last_error="systemd unit camera-agent.service is not active")
    body = client.get(detail_url, headers=headers).json()
    assert body["status"] == "completed"
    assert body["counts"]["succeeded"] == 1
    assert body["devices"][0]["status"] == "succeeded"
    assert body["devices"][0]["health"] == "unhealthy"


def test_deploy_detail_shows_endpoint_availability(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-url", mac="aa:bb:cc:dd:ee:42")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [reg["device_id"]]},
            "endpoint": {"hostname": "camera-health.meteor-edge.com", "port": 80},
        },
    )
    assert created.status_code == 201, created.text
    detail = client.get(f"/api/v1/organizations/{org.id}/deployments/{created.json()['id']}", headers=headers)
    row = detail.json()["devices"][0]
    assert row["endpoint_availability"] == "unknown"
    assert row["health"] == "unknown"
