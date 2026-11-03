"""Tests for Deploy APIs (Phase 3): targeting, RBAC, cancel, progress."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import event
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


def _docker_web_release(
    client: TestClient, org_id, headers, *, name: str = "camera-agent", ports: tuple[str, ...] = ("80:80",)
) -> dict:
    """An image-pull docker release; only container kinds can be published on the internet."""
    created = client.post(
        f"/api/v1/organizations/{org_id}/releases",
        headers=headers,
        json={
            "name": name,
            "version": "1.0.0",
            "kind": "docker",
            "spec": {"container_name": name, "image": "nginx:alpine", "ports": list(ports)},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


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
    assert body["devices"][0]["last_error"] == "systemd unit camera-agent.service is not active"


def test_deploy_detail_shows_endpoint_availability(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    release = _docker_web_release(client, org.id, headers)
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


def test_deploy_detail_loads_software_rows_in_one_query(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device_ids = [
        _register_device(client, token, name=f"edge-batch-{n}", mac=f"aa:bb:cc:dd:ee:5{n}")["device_id"]
        for n in range(3)
    ]
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": device_ids}},
    ).json()
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        detail = client.get(f"/api/v1/organizations/{org.id}/deployments/{created['id']}", headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert detail.status_code == 200
    assert len(detail.json()["devices"]) == 3
    software_selects = [sql for sql in statements if sql.startswith("SELECT") and "FROM device_software" in sql]
    assert len(software_selects) == 1


def _phased(client: TestClient, org, headers: dict, release: dict, device_ids: list[str], **rollout) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": device_ids},
            "rollout": {
                "strategy": "phased",
                "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 3600}],
                **rollout,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _action(client: TestClient, org, headers: dict, deployment_id: str, action: str):
    return client.post(f"/api/v1/organizations/{org.id}/deployments/{deployment_id}/{action}", headers=headers)


def test_pause_and_resume_phased_deploy(client: TestClient, db_session: Session) -> None:
    from app.audit.models import AuditEvent

    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"pz-{index}", mac=f"aa:bb:cc:70:00:0{index}") for index in range(3)]
    body = _phased(client, org, headers, release, [reg["device_id"] for reg in regs])

    paused = _action(client, org, headers, body["id"], "pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert paused.json()["rollout"]["state"]["paused_reason"] == "manual"
    assert paused.json()["rollout"]["state"]["paused_at"]

    # Activated devices keep reporting while paused; nothing new is activated.
    canary = next(reg for reg in regs if reg["device_id"] == body["rollout"]["activated_device_ids"][0])
    _report(client, canary, release, health="healthy")
    detail = client.get(f"/api/v1/organizations/{org.id}/deployments/{body['id']}", headers=headers).json()
    assert detail["status"] == "paused"
    assert detail["counts"]["succeeded"] == 1
    assert detail["counts"]["queued"] == 2

    assert _action(client, org, headers, body["id"], "pause").status_code == 409

    resumed = _action(client, org, headers, body["id"], "resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "in_progress"
    assert "paused_reason" not in resumed.json()["rollout"]["state"]
    assert _action(client, org, headers, body["id"], "resume").status_code == 409

    actions = {
        row.action: row.metadata_
        for row in db_session.query(AuditEvent).filter(AuditEvent.organization_id == org.id).all()
    }
    assert actions["deployment.pause"]["reason"] == "manual"
    assert actions["deployment.resume"]["reason"] == "manual"


def test_resume_runs_a_tick_immediately(client: TestClient, db_session: Session) -> None:
    from uuid import UUID

    from app.deployments.models import Deployment

    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"pr-{index}", mac=f"aa:bb:cc:71:00:0{index}") for index in range(3)]
    body = _phased(client, org, headers, release, [reg["device_id"] for reg in regs])
    canary = next(reg for reg in regs if reg["device_id"] == body["rollout"]["activated_device_ids"][0])
    _report(client, canary, release, health="healthy")
    assert _action(client, org, headers, body["id"], "pause").status_code == 200

    row = db_session.get(Deployment, UUID(body["id"]))
    db_session.refresh(row)
    rollout = dict(row.rollout)
    rollout["state"] = {**rollout["state"], "phase_completed_at": "2020-01-01T00:00:00+00:00"}
    row.rollout = rollout
    db_session.commit()

    resumed = _action(client, org, headers, body["id"], "resume").json()
    assert resumed["counts"]["queued"] == 0
    assert resumed["counts"]["pending"] == 2


def test_pause_and_resume_wrong_state_and_strategy(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pw-0", mac="aa:bb:cc:72:00:01")
    immediate = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    ).json()
    not_pausable = _action(client, org, headers, immediate["id"], "pause")
    assert not_pausable.status_code == 409
    assert not_pausable.json()["error"]["code"] == "deployment_not_pausable"
    assert _action(client, org, headers, immediate["id"], "resume").status_code == 409

    _report(client, reg, release, health="healthy")
    assert _action(client, org, headers, immediate["id"], "pause").status_code == 409

    viewer = create_user(db_session, email="viewer-pause@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    phased = _phased(client, org, headers, release, [reg["device_id"]])
    denied = _action(client, org, auth_header(client, "viewer-pause@example.com"), phased["id"], "pause")
    assert denied.status_code == 403


def test_cancel_while_paused(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"pc-{index}", mac=f"aa:bb:cc:73:00:0{index}") for index in range(3)]
    body = _phased(client, org, headers, release, [reg["device_id"] for reg in regs])
    assert _action(client, org, headers, body["id"], "pause").status_code == 200

    cancelled = _action(client, org, headers, body["id"], "cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["counts"]["cancelled"] == 3
    assert _action(client, org, headers, body["id"], "resume").status_code == 409

    canary = body["rollout"]["activated_device_ids"][0]
    software = client.get(f"/api/v1/organizations/{org.id}/devices/{canary}/software", headers=headers).json()
    assert software[0]["sync_status"] == "cancelled"


def test_rollback_while_paused_only_targets_activated(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"pb-{index}", mac=f"aa:bb:cc:74:00:0{index}") for index in range(3)]
    device_ids = [reg["device_id"] for reg in regs]
    client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": device_ids}},
    )
    for reg in regs:
        _report(client, reg, release, health="healthy")

    artifact = client.post(
        f"/api/v1/organizations/{org.id}/artifacts",
        headers=headers,
        data={"name": "camera-agent", "version": "3.0", "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    ).json()
    release2 = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": "3.0.0",
            "kind": "systemd",
            "artifact_id": artifact["id"],
            "spec": {"unit_name": "camera-agent.service"},
        },
    ).json()
    body = _phased(client, org, headers, release2, device_ids, failure_threshold_percent=10)
    canary = next(reg for reg in regs if reg["device_id"] == body["rollout"]["activated_device_ids"][0])
    _report(client, canary, release2, health="unhealthy", last_error="crash")
    detail = client.get(f"/api/v1/organizations/{org.id}/deployments/{body['id']}", headers=headers).json()
    assert detail["status"] == "paused"
    assert detail["rollout"]["state"]["paused_reason"] == "failure_threshold"

    rolled = _action(client, org, headers, body["id"], "rollback")
    assert rolled.status_code == 201, rolled.text
    assert rolled.json()["deployments"][0]["release_id"] == release["id"]
    assert rolled.json()["deployments"][0]["target"]["device_ids"] == [canary["device_id"]]
    # Resuming or retrying the original would write the rolled-back release again.
    for action in ("resume", "retry-failed"):
        refused = _action(client, org, headers, body["id"], action)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "deployment_rolled_back"


def test_retry_failed_keeps_paused_deploy_paused(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"pf-{index}", mac=f"aa:bb:cc:75:00:0{index}") for index in range(2)]
    body = _phased(client, org, headers, release, [reg["device_id"] for reg in regs], failure_threshold_percent=10)
    canary = next(reg for reg in regs if reg["device_id"] == body["rollout"]["activated_device_ids"][0])
    response = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {canary['device_token']}"},
        json={
            "software_report": [
                {"workload_key": "camera-agent.service", "sync_status": "failed", "health": "unhealthy"}
            ]
        },
    )
    assert response.status_code == 200, response.text
    retried = _action(client, org, headers, body["id"], "retry-failed")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "paused"
    assert retried.json()["counts"]["pending"] == 1


def test_sweeper_times_out_silent_devices_and_advances_batched_delay(
    client: TestClient, db_session: Session
) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from app.deployments.models import Deployment, DeploymentDevice
    from app.deployments.sweeper import sweep_deployments

    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"sw-{index}", mac=f"aa:bb:cc:76:00:0{index}") for index in range(4)]
    phased = _phased(
        client,
        org,
        headers,
        release,
        [regs[0]["device_id"], regs[1]["device_id"]],
        pending_timeout_seconds=60,
    )
    batched = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [regs[2]["device_id"], regs[3]["device_id"]]},
            "rollout": {"strategy": "batched", "batch_size": 1, "delay_seconds": 600},
        },
    ).json()
    batched_first = next(reg for reg in regs if reg["device_id"] == batched["rollout"]["activated_device_ids"][0])
    _report(client, batched_first, release, health="healthy")

    old = datetime.now(UTC) - timedelta(hours=1)
    db_session.expire_all()
    for row in db_session.query(DeploymentDevice).filter(DeploymentDevice.deployment_id == UUID(phased["id"])):
        if row.activated_at is not None:
            row.activated_at = old
    batched_row = db_session.get(Deployment, UUID(batched["id"]))
    batched_row.rollout = {**batched_row.rollout, "batch_released_at": old.isoformat()}
    db_session.commit()

    assert sweep_deployments(db_session) == 2

    get = lambda deployment_id: client.get(  # noqa: E731
        f"/api/v1/organizations/{org.id}/deployments/{deployment_id}", headers=headers
    ).json()
    swept = get(phased["id"])
    assert swept["counts"]["timed_out"] == 1
    # The canary timed out without reporting; the soak delay then holds the next phase.
    assert swept["counts"]["queued"] == 1
    assert swept["rollout"]["state"]["phase_completed_at"]
    advanced = get(batched["id"])
    assert advanced["counts"]["queued"] == 0
    assert advanced["counts"]["pending"] == 1
    assert advanced["counts"]["succeeded"] == 1


def test_sweeper_runs_on_one_replica_and_releases_its_lock(
    client: TestClient, db_session: Session, engine
) -> None:
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from app.deployments.sweeper import SWEEP_LOCK_KEY, sweep_once

    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="sw-lock", mac="aa:bb:cc:77:00:01")
    _phased(client, org, headers, release, [reg["device_id"]])
    factory = sessionmaker(bind=engine)

    with engine.connect() as other_replica:
        assert other_replica.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_LOCK_KEY})
        assert sweep_once(engine, factory) == 0
        other_replica.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY})
        other_replica.commit()

    assert sweep_once(engine, factory) == 1
    with engine.connect() as other_replica:
        assert other_replica.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_LOCK_KEY})
        other_replica.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY})
        other_replica.commit()


def test_sweeper_failure_on_one_deploy_does_not_stop_the_others(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    from uuid import UUID

    from app.deployments.service import DeploymentService
    from app.deployments.sweeper import sweep_deployments

    org, headers, token, release = _setup_release(client, db_session)
    regs = [_register_device(client, token, name=f"sf-{index}", mac=f"aa:bb:cc:78:00:0{index}") for index in range(2)]
    # Deploys are swept in id order, so the failing one runs first.
    bodies = [_phased(client, org, headers, release, [reg["device_id"]]) for reg in regs]
    first = min(bodies, key=lambda body: body["id"])
    original = DeploymentService.tick_deployment

    def flaky_tick(self, deployment):
        if deployment.id == UUID(first["id"]):
            raise RuntimeError("boom")
        return original(self, deployment)

    monkeypatch.setattr(DeploymentService, "tick_deployment", flaky_tick)
    assert sweep_deployments(db_session) == 1
