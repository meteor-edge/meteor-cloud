"""Phase 8: batched rollout, retry-failed, rollback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deployments.models import Deployment, DeploymentDevice
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_deployments import UNIT, _register_device, _setup_release


def _report_in_sync(client: TestClient, device_token: str, release_id: str, version: str) -> None:
    response = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {device_token}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release_id,
                    "installed_version": version,
                    "sync_status": "in_sync",
                    "progress_percent": 100,
                    "health": "healthy",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text


def _second_release(client: TestClient, org_id, headers: dict) -> dict:
    artifact = client.post(
        f"/api/v1/organizations/{org_id}/artifacts",
        headers=headers,
        data={"name": "camera-agent", "version": "2.0", "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    ).json()
    return client.post(
        f"/api/v1/organizations/{org_id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": "2.0.0",
            "kind": "systemd",
            "artifact_id": artifact["id"],
            "spec": {"unit_name": "camera-agent.service"},
        },
    ).json()


def test_batched_rollout_then_retry_and_rollback(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [
        _register_device(client, token, name=f"pi-{index}", mac=f"aa:bb:cc:10:00:0{index}")
        for index in range(4)
    ]
    device_ids = [reg["device_id"] for reg in regs]

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": device_ids},
            "rollout": {"strategy": "batched", "batch_size": 2},
        },
    )
    assert created.status_code == 201, created.text
    deployment = created.json()
    assert deployment["counts"]["pending"] == 2
    assert deployment["counts"]["queued"] == 2

    activated = set(deployment["rollout"]["activated_device_ids"])
    for reg in regs:
        if reg["device_id"] in activated:
            _report_in_sync(client, reg["device_token"], release["id"], "1.2.0")

    detail = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{deployment['id']}",
        headers=headers,
    ).json()
    assert detail["counts"]["succeeded"] == 2
    assert detail["counts"]["pending"] == 2
    assert detail["counts"]["queued"] == 0
    assert detail["status"] == "in_progress"

    pending = [row for row in detail["devices"] if row["status"] == "pending"]
    fail_reg = next(reg for reg in regs if reg["device_id"] == pending[0]["device_id"])
    failed = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {fail_reg['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "sync_status": "failed",
                    "progress_percent": 0,
                    "health": "unhealthy",
                    "last_error": "boom",
                }
            ]
        },
    )
    assert failed.status_code == 200, failed.text

    retried = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{deployment['id']}/retry-failed",
        headers=headers,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["counts"]["failed"] == 0

    for row in retried.json()["devices"]:
        if row["status"] not in {"pending", "updating"}:
            continue
        reg = next(item for item in regs if item["device_id"] == row["device_id"])
        _report_in_sync(client, reg["device_token"], release["id"], "1.2.0")

    first_done = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{deployment['id']}",
        headers=headers,
    ).json()
    assert first_done["status"] == "completed"

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
    second = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release2["id"],
            "target": {"device_ids": device_ids},
            "rollout": {"strategy": "immediate"},
        },
    )
    assert second.status_code == 201, second.text
    for reg in regs:
        _report_in_sync(client, reg["device_token"], release2["id"], "2.0.0")

    finished = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{second.json()['id']}",
        headers=headers,
    ).json()
    assert finished["status"] == "completed"

    rolled = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{finished['id']}/rollback",
        headers=headers,
    )
    assert rolled.status_code == 201, rolled.text
    body = rolled.json()
    assert body["release_id"] == release["id"]
    assert body["rollback_of_deployment_id"] == finished["id"]

    developer = create_user(db_session, email="dev-roll@example.com")
    add_member(db_session, org, developer, OrganizationRole.DEVELOPER)
    denied = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{finished['id']}/rollback",
        headers=auth_header(client, "dev-roll@example.com"),
    )
    assert denied.status_code == 403


def test_batched_delay_advances_on_later_heartbeat_without_open_rows(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [
        _register_device(client, token, name=f"pi-d{index}", mac=f"aa:bb:cc:20:00:0{index}")
        for index in range(3)
    ]
    device_ids = [reg["device_id"] for reg in regs]

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": device_ids},
            "rollout": {"strategy": "batched", "batch_size": 2, "delay_seconds": 3600},
        },
    )
    assert created.status_code == 201, created.text
    deployment = created.json()
    activated = set(deployment["rollout"]["activated_device_ids"])
    assert len(activated) == 2

    for reg in regs:
        if reg["device_id"] in activated:
            _report_in_sync(client, reg["device_token"], release["id"], "1.2.0")

    detail = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{deployment['id']}",
        headers=headers,
    ).json()
    assert detail["counts"]["succeeded"] == 2
    assert detail["counts"]["queued"] == 1

    row = db_session.get(Deployment, UUID(deployment["id"]))
    assert row is not None
    rollout = dict(row.rollout or {})
    rollout["batch_released_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    row.rollout = rollout
    db_session.commit()

    done_reg = next(reg for reg in regs if reg["device_id"] in activated)
    _report_in_sync(client, done_reg["device_token"], release["id"], "1.2.0")

    advanced = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{deployment['id']}",
        headers=headers,
    ).json()
    assert advanced["counts"]["queued"] == 0
    assert advanced["counts"]["pending"] + advanced["counts"]["succeeded"] == 3


def _rows(db_session: Session, deployment_id: str) -> list[DeploymentDevice]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(DeploymentDevice).where(DeploymentDevice.deployment_id == UUID(deployment_id))
        ).all()
    )


def test_activated_at_set_for_first_wave_and_later_batches(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [
        _register_device(client, token, name=f"pi-a{index}", mac=f"aa:bb:cc:30:00:0{index}")
        for index in range(3)
    ]
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [reg["device_id"] for reg in regs]},
            "rollout": {"strategy": "batched", "batch_size": 2},
        },
    ).json()
    activated = set(created["rollout"]["activated_device_ids"])
    by_device = {row["device_id"]: row for row in created["devices"]}
    for device_id, row in by_device.items():
        if device_id in activated:
            assert row["activated_at"] is not None
        else:
            assert row["status"] == "queued"
            assert row["activated_at"] is None

    for reg in regs:
        if reg["device_id"] in activated:
            _report_in_sync(client, reg["device_token"], release["id"], "1.2.0")

    rows = _rows(db_session, created["id"])
    assert all(row.activated_at is not None for row in rows)
    later = next(row for row in rows if str(row.device_id) not in activated)
    first = next(row for row in rows if str(row.device_id) in activated)
    assert later.status == "pending"
    assert later.activated_at >= first.activated_at


def test_rollback_of_batched_deploy_only_targets_activated_devices(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [
        _register_device(client, token, name=f"pi-r{index}", mac=f"aa:bb:cc:40:00:0{index}")
        for index in range(4)
    ]
    device_ids = [reg["device_id"] for reg in regs]
    first = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": device_ids}},
    )
    assert first.status_code == 201, first.text
    for reg in regs:
        _report_in_sync(client, reg["device_token"], release["id"], "1.2.0")

    release2 = _second_release(client, org.id, headers)
    second = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release2["id"],
            "target": {"device_ids": device_ids},
            "rollout": {"strategy": "batched", "batch_size": 2},
        },
    ).json()
    activated = set(second["rollout"]["activated_device_ids"])
    assert len(activated) == 2
    cancelled = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{second['id']}/cancel", headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text

    rolled = client.post(
        f"/api/v1/organizations/{org.id}/deployments/{second['id']}/rollback", headers=headers
    )
    assert rolled.status_code == 201, rolled.text
    body = rolled.json()
    assert body["release_id"] == release["id"]
    assert set(body["target"]["device_ids"]) == activated
    assert {row["device_id"] for row in body["devices"]} == activated


def test_counts_include_timed_out(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = [
        _register_device(client, token, name=f"pi-t{index}", mac=f"aa:bb:cc:50:00:0{index}")
        for index in range(2)
    ]
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"] for reg in regs]}},
    ).json()
    rows = _rows(db_session, created["id"])
    rows[0].status = "timed_out"
    db_session.commit()

    detail = client.get(
        f"/api/v1/organizations/{org.id}/deployments/{created['id']}", headers=headers
    ).json()
    assert detail["counts"]["timed_out"] == 1
    assert detail["counts"]["pending"] == 1
    assert detail["counts"]["total"] == 2
