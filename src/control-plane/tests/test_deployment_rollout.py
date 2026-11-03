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
    assert len(rolled.json()["deployments"]) == 1
    assert rolled.json()["skipped"] == []
    body = rolled.json()["deployments"][0]
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
    body = rolled.json()["deployments"][0]
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


def _phased_deploy(
    client: TestClient, org_id, headers: dict, release: dict, regs: list[dict], **rollout
) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org_id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [reg["device_id"] for reg in regs]},
            "rollout": {"strategy": "phased", **rollout},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _devices(client: TestClient, token: str, count: int, prefix: str) -> list[dict]:
    return [
        _register_device(client, token, name=f"pi-{prefix}{index}", mac=f"aa:bb:{prefix}:00:00:0{index}")
        for index in range(count)
    ]


def _report_health(client: TestClient, reg: dict, release: dict, *, health: str, sync_status: str = "in_sync") -> None:
    response = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release["id"],
                    "installed_version": release["version"],
                    "sync_status": sync_status,
                    "progress_percent": 100,
                    "health": health,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text


def _tick(db_session: Session, deployment_id: str) -> Deployment:
    from app.deployments.service import DeploymentService

    db_session.expire_all()
    row = db_session.get(Deployment, UUID(deployment_id))
    assert row is not None
    ticked = DeploymentService(db_session).tick_deployment(row)
    db_session.commit()
    return ticked


def _detail(client: TestClient, org_id, headers: dict, deployment_id: str) -> dict:
    response = client.get(f"/api/v1/organizations/{org_id}/deployments/{deployment_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _reg_for(regs: list[dict], device_id: str) -> dict:
    return next(reg for reg in regs if reg["device_id"] == device_id)


def _set_state(db_session: Session, deployment_id: str, **values) -> None:
    db_session.expire_all()
    row = db_session.get(Deployment, UUID(deployment_id))
    assert row is not None
    rollout = dict(row.rollout)
    rollout["state"] = {**rollout.get("state", {}), **values}
    row.rollout = rollout
    db_session.commit()


CANARY_THEN_ALL = [{"count": 1}, {"percent": 100, "delay_seconds": 3600}]


def test_phased_create_activates_only_the_canary(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "c1")
    body = _phased_deploy(client, org.id, headers, release, regs, phases=CANARY_THEN_ALL)
    assert body["status"] == "in_progress"
    assert body["counts"]["pending"] == 1
    assert body["counts"]["queued"] == 2
    assert body["rollout"]["state"]["current_phase"] == 0
    assert len(body["rollout"]["activated_device_ids"]) == 1


def test_phased_delay_counts_from_phase_completion(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "c2")
    body = _phased_deploy(client, org.id, headers, release, regs, phases=CANARY_THEN_ALL)
    canary = _reg_for(regs, body["rollout"]["activated_device_ids"][0])
    # Phase 1 started long ago; only its completion time matters.
    _set_state(db_session, body["id"], phase_started_at=(datetime.now(UTC) - timedelta(days=2)).isoformat())
    _report_in_sync(client, canary["device_token"], release["id"], "1.2.0")

    detail = _detail(client, org.id, headers, body["id"])
    assert detail["counts"]["succeeded"] == 1
    assert detail["counts"]["queued"] == 2
    assert detail["rollout"]["state"]["phase_completed_at"] is not None

    _set_state(db_session, body["id"], phase_completed_at=(datetime.now(UTC) - timedelta(minutes=30)).isoformat())
    _tick(db_session, body["id"])
    assert _detail(client, org.id, headers, body["id"])["counts"]["queued"] == 2

    _set_state(db_session, body["id"], phase_completed_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat())
    _tick(db_session, body["id"])
    advanced = _detail(client, org.id, headers, body["id"])
    assert advanced["counts"]["queued"] == 0
    assert advanced["counts"]["pending"] == 2
    assert advanced["rollout"]["state"]["current_phase"] == 1


def test_phased_pauses_when_failure_threshold_is_reached(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "c3")
    body = _phased_deploy(
        client, org.id, headers, release, regs, phases=CANARY_THEN_ALL, failure_threshold_percent=10
    )
    canary = _reg_for(regs, body["rollout"]["activated_device_ids"][0])
    _report_health(client, canary, release, health="unhealthy", sync_status="failed")

    detail = _detail(client, org.id, headers, body["id"])
    assert detail["status"] == "paused"
    assert detail["rollout"]["state"]["paused_reason"] == "failure_threshold"
    assert detail["rollout"]["state"]["paused_at"]
    assert detail["counts"]["failed"] == 1
    assert detail["counts"]["queued"] == 2


def test_phased_pauses_mid_phase_with_max_concurrent(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 5, "c4")
    body = _phased_deploy(
        client,
        org.id,
        headers,
        release,
        regs,
        phases=[{"percent": 100}],
        max_concurrent=2,
        failure_threshold_percent=50,
    )
    assert body["counts"]["pending"] == 2
    assert body["counts"]["queued"] == 3

    first, second = body["rollout"]["activated_device_ids"]
    _report_in_sync(client, _reg_for(regs, first)["device_token"], release["id"], "1.2.0")
    detail = _detail(client, org.id, headers, body["id"])
    assert detail["counts"]["succeeded"] == 1
    assert detail["counts"]["pending"] == 2
    assert detail["counts"]["queued"] == 2

    _report_health(client, _reg_for(regs, second), release, health="unhealthy", sync_status="failed")
    paused = _detail(client, org.id, headers, body["id"])
    assert paused["status"] == "paused"
    assert paused["rollout"]["state"]["paused_reason"] == "failure_threshold"
    assert paused["rollout"]["state"]["current_phase"] == 0
    assert paused["counts"]["queued"] == 2


def test_phased_in_sync_but_unhealthy_is_a_failure(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "c5")
    body = _phased_deploy(
        client, org.id, headers, release, regs, phases=CANARY_THEN_ALL, failure_threshold_percent=10
    )
    canary = _reg_for(regs, body["rollout"]["activated_device_ids"][0])
    _report_health(client, canary, release, health="unhealthy")
    detail = _detail(client, org.id, headers, body["id"])
    assert detail["counts"]["failed"] == 1
    assert detail["status"] == "paused"


def test_device_turning_unhealthy_during_soak_trips_the_budget(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "c6")
    body = _phased_deploy(
        client, org.id, headers, release, regs, phases=CANARY_THEN_ALL, failure_threshold_percent=10
    )
    canary = _reg_for(regs, body["rollout"]["activated_device_ids"][0])
    _report_health(client, canary, release, health="healthy")
    soaking = _detail(client, org.id, headers, body["id"])
    assert soaking["status"] == "in_progress"
    assert soaking["counts"]["succeeded"] == 1

    _report_health(client, canary, release, health="unhealthy")
    paused = _detail(client, org.id, headers, body["id"])
    assert paused["status"] == "paused"
    assert paused["rollout"]["state"]["paused_reason"] == "failure_threshold"
    # The row keeps its install result; current health is what counts against the budget.
    assert paused["counts"]["succeeded"] == 1
    assert paused["counts"]["queued"] == 2


def _age_activation(db_session: Session, deployment_id: str, device_id: str, seconds: int) -> None:
    db_session.expire_all()
    row = db_session.scalar(
        select(DeploymentDevice).where(
            DeploymentDevice.deployment_id == UUID(deployment_id),
            DeploymentDevice.device_id == UUID(device_id),
        )
    )
    assert row is not None and row.activated_at is not None
    row.activated_at = row.activated_at - timedelta(seconds=seconds)
    db_session.commit()


def test_timed_out_is_terminal_and_excluded_from_failure_rate(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 4, "c7")
    body = _phased_deploy(
        client,
        org.id,
        headers,
        release,
        regs,
        phases=[{"count": 2}, {"percent": 100}],
        failure_threshold_percent=50,
        pending_timeout_seconds=60,
    )
    online, offline = body["rollout"]["activated_device_ids"]
    _report_in_sync(client, _reg_for(regs, online)["device_token"], release["id"], "1.2.0")
    assert _detail(client, org.id, headers, body["id"])["counts"]["queued"] == 2

    _age_activation(db_session, body["id"], offline, 120)
    _tick(db_session, body["id"])
    detail = _detail(client, org.id, headers, body["id"])
    assert detail["status"] == "in_progress"
    assert detail["counts"]["timed_out"] == 1
    assert detail["counts"]["queued"] == 0
    assert detail["counts"]["pending"] == 2
    timed_out = next(row for row in detail["devices"] if row["device_id"] == offline)
    assert timed_out["status"] == "timed_out"

    # The offline device comes back and still picks up the release.
    _report_in_sync(client, _reg_for(regs, offline)["device_token"], release["id"], "1.2.0")
    late = _detail(client, org.id, headers, body["id"])
    assert late["counts"]["timed_out"] == 0
    assert late["counts"]["succeeded"] == 2


def test_late_report_does_not_reopen_a_finished_deploy(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 1, "c8")
    body = _phased_deploy(
        client, org.id, headers, release, regs, phases=[{"percent": 100}], pending_timeout_seconds=60
    )
    device_id = regs[0]["device_id"]
    _age_activation(db_session, body["id"], device_id, 120)
    _tick(db_session, body["id"])
    finished = _detail(client, org.id, headers, body["id"])
    assert finished["status"] == "failed"
    assert finished["counts"]["timed_out"] == 1

    _report_health(client, regs[0], release, health="healthy", sync_status="updating")
    assert _detail(client, org.id, headers, body["id"])["counts"]["timed_out"] == 1

    _report_in_sync(client, regs[0]["device_token"], release["id"], "1.2.0")
    late = _detail(client, org.id, headers, body["id"])
    assert late["status"] == "failed"
    assert late["counts"]["succeeded"] == 1


def test_canary_picks_the_most_recently_seen_device(client: TestClient, db_session: Session) -> None:
    from app.devices.models import Device

    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "c9")
    now = datetime.now(UTC)
    seen = {regs[0]["device_id"]: None, regs[1]["device_id"]: now - timedelta(hours=1), regs[2]["device_id"]: now}
    for device_id, last_seen in seen.items():
        device = db_session.get(Device, UUID(device_id))
        assert device is not None
        device.last_seen_at = last_seen
    db_session.commit()

    body = _phased_deploy(client, org.id, headers, release, regs, phases=CANARY_THEN_ALL)
    assert body["rollout"]["activated_device_ids"] == [regs[2]["device_id"]]
    row = next(item for item in body["devices"] if item["device_id"] == regs[2]["device_id"])
    assert row["device_last_seen_at"] is not None


def test_ticks_in_sequence_do_not_double_activate(client: TestClient, db_session: Session) -> None:
    from app.deployments.software_models import DeviceSoftware

    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 4, "ca")
    body = _phased_deploy(
        client, org.id, headers, release, regs, phases=[{"count": 1}, {"count": 2}, {"percent": 100}]
    )
    canary = body["rollout"]["activated_device_ids"][0]
    db_session.expire_all()
    row = db_session.scalar(
        select(DeploymentDevice).where(
            DeploymentDevice.deployment_id == UUID(body["id"]),
            DeploymentDevice.device_id == UUID(canary),
        )
    )
    assert row is not None
    row.status = "succeeded"
    db_session.commit()

    _tick(db_session, body["id"])
    _tick(db_session, body["id"])
    detail = _detail(client, org.id, headers, body["id"])
    assert detail["counts"]["pending"] == 1
    assert detail["counts"]["queued"] == 2
    assert len(detail["rollout"]["activated_device_ids"]) == 2
    assert detail["rollout"]["state"]["current_phase"] == 1
    second = next(value for value in detail["rollout"]["activated_device_ids"] if value != canary)
    software = db_session.scalar(select(DeviceSoftware).where(DeviceSoftware.device_id == UUID(second)))
    assert software is not None
    assert software.desired_revision == 1


def test_phased_fields_rejected_for_batched(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 1, "cb")
    response = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [regs[0]["device_id"]]},
            "rollout": {"strategy": "batched", "batch_size": 1, "failure_threshold_percent": 10},
        },
    )
    assert response.status_code == 422
    assert "only supported with strategy 'phased'" in response.text


def test_tick_and_heartbeat_in_parallel_sessions(client: TestClient, db_session: Session, engine) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.deployments.service import DeploymentService
    from app.deployments.software_models import DeviceSoftware

    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 3, "cc")
    body = _phased_deploy(client, org.id, headers, release, regs, phases=[{"count": 1}, {"percent": 100}])
    canary = UUID(body["rollout"]["activated_device_ids"][0])
    db_session.expire_all()
    software = db_session.scalar(select(DeviceSoftware).where(DeviceSoftware.device_id == canary))
    assert software is not None
    # The heartbeat already wrote device_software; only the Deploy rows are left to sync.
    software.sync_status = "in_sync"
    software.health = "healthy"
    workload_key = software.workload_key
    db_session.commit()
    start = threading.Barrier(2)

    def run(action: str) -> str:
        start.wait(timeout=5)
        with Session(engine) as session:
            service = DeploymentService(session)
            if action == "heartbeat":
                service.sync_progress_from_software(
                    organization_id=org.id, device_id=canary, workload_key=workload_key
                )
            else:
                deployment = session.get(Deployment, UUID(body["id"]))
                assert deployment is not None
                service.tick_deployment(deployment)
            session.commit()
        return action

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["heartbeat", "tick"]))
    assert sorted(results) == ["heartbeat", "tick"]

    detail = _detail(client, org.id, headers, body["id"])
    assert detail["counts"]["succeeded"] == 1
    assert detail["counts"]["pending"] == 2
    assert len(detail["rollout"]["activated_device_ids"]) == 3
    db_session.expire_all()
    revisions = db_session.scalars(
        select(DeviceSoftware.desired_revision).where(DeviceSoftware.device_id != canary)
    ).all()
    assert revisions == [1, 1]


def test_cancel_keeps_desired_written_by_a_newer_deploy(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    regs = _devices(client, token, 2, "cd")
    body = _phased_deploy(
        client, org.id, headers, release, regs, phases=CANARY_THEN_ALL, pending_timeout_seconds=60
    )
    canary = body["rollout"]["activated_device_ids"][0]
    _age_activation(db_session, body["id"], canary, 120)
    _tick(db_session, body["id"])
    assert _detail(client, org.id, headers, body["id"])["counts"]["timed_out"] == 1

    release2 = _second_release(client, org.id, headers)
    newer = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release2["id"], "target": {"device_ids": [canary]}},
    )
    assert newer.status_code == 201, newer.text

    cancelled = client.post(f"/api/v1/organizations/{org.id}/deployments/{body['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    software = client.get(f"/api/v1/organizations/{org.id}/devices/{canary}/software", headers=headers).json()
    assert software[0]["desired_release_id"] == release2["id"]
