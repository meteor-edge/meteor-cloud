"""Rollback returns each device to its own previous release; kinds never replace each other."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_deployments import UNIT, _docker_web_release, _register_device, _report, _setup_release


def _systemd_release(client: TestClient, org_id, headers, version: str) -> dict:
    artifact = client.post(
        f"/api/v1/organizations/{org_id}/artifacts",
        headers=headers,
        data={"name": "camera-agent", "version": version, "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    ).json()
    created = client.post(
        f"/api/v1/organizations/{org_id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": version,
            "kind": "systemd",
            "artifact_id": artifact["id"],
            "spec": {"unit_name": "camera-agent.service"},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _deploy(client: TestClient, org_id, headers, release: dict, device_ids: list[str]) -> dict:
    created = client.post(
        f"/api/v1/organizations/{org_id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": device_ids}},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_rollback_returns_each_device_to_its_own_previous_release(client: TestClient, db_session: Session) -> None:
    org, headers, token, v1 = _setup_release(client, db_session)  # camera-agent 1.2.0
    a, b, c = (
        _register_device(client, token, name=f"rb-{name}", mac=f"aa:bb:cc:dd:50:0{index}")
        for index, name in enumerate("abc")
    )
    v2 = _systemd_release(client, org.id, headers, "2.0.0")
    v3 = _systemd_release(client, org.id, headers, "3.0.0")
    _deploy(client, org.id, headers, v1, [a["device_id"]])
    _deploy(client, org.id, headers, v2, [b["device_id"]])
    bad = _deploy(client, org.id, headers, v3, [a["device_id"], b["device_id"], c["device_id"]])
    for reg in (a, b, c):
        _report(client, reg, v3, health="unhealthy", last_error="crash")

    rolled = client.post(f"/api/v1/organizations/{org.id}/deployments/{bad['id']}/rollback", headers=headers)
    assert rolled.status_code == 201, rolled.text
    body = rolled.json()
    by_release = {item["release_id"]: item["target"]["device_ids"] for item in body["deployments"]}
    assert by_release == {v1["id"]: [a["device_id"]], v2["id"]: [b["device_id"]]}
    assert all(item["rollback_of_deployment_id"] == bad["id"] for item in body["deployments"])
    # rb-c had nothing before: it is left alone instead of getting someone else's version.
    assert [(item["device_id"], item["device_name"]) for item in body["skipped"]] == [(c["device_id"], "rb-c")]

    software = client.get(f"/api/v1/organizations/{org.id}/devices/{b['device_id']}/software", headers=headers).json()
    assert software[0]["desired_release_id"] == v2["id"]


def test_rollback_reports_a_group_that_fails_after_another_was_created(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    from app.core.exceptions import ConflictError
    from app.deployments.service import DeploymentService

    org, headers, token, v1 = _setup_release(client, db_session)
    a, b = (
        _register_device(client, token, name=f"rbf-{name}", mac=f"aa:bb:cc:dd:51:0{index}")
        for index, name in enumerate("ab")
    )
    v2 = _systemd_release(client, org.id, headers, "2.0.0")
    v3 = _systemd_release(client, org.id, headers, "3.0.0")
    _deploy(client, org.id, headers, v1, [a["device_id"]])
    _deploy(client, org.id, headers, v2, [b["device_id"]])
    bad = _deploy(client, org.id, headers, v3, [a["device_id"], b["device_id"]])
    for reg in (a, b):
        _report(client, reg, v3, health="unhealthy", last_error="crash")

    real_create = DeploymentService.create_deployment
    calls = {"n": 0}

    def flaky_create(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConflictError("simulated_failure", "Second group failed.")
        return real_create(self, **kwargs)

    monkeypatch.setattr(DeploymentService, "create_deployment", flaky_create)
    rolled = client.post(f"/api/v1/organizations/{org.id}/deployments/{bad['id']}/rollback", headers=headers)
    assert rolled.status_code == 201, rolled.text
    body = rolled.json()
    assert len(body["deployments"]) == 1
    applied = body["deployments"][0]["release_id"]
    assert [item["code"] for item in body["failed"]] == ["simulated_failure"]
    failed = body["failed"][0]
    assert {applied, failed["release_id"]} == {v1["id"], v2["id"]}
    assert failed["device_ids"] == [a["device_id"] if failed["release_id"] == v1["id"] else b["device_id"]]


def test_rollback_fails_cleanly_when_the_first_group_fails(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    from app.core.exceptions import ConflictError
    from app.deployments.service import DeploymentService

    org, headers, token, v1 = _setup_release(client, db_session)
    device = _register_device(client, token, name="rbf-one", mac="aa:bb:cc:dd:51:10")
    v2 = _systemd_release(client, org.id, headers, "2.0.0")
    _deploy(client, org.id, headers, v1, [device["device_id"]])
    bad = _deploy(client, org.id, headers, v2, [device["device_id"]])
    _report(client, device, v2, health="unhealthy", last_error="crash")

    def failing_create(self, **kwargs):
        raise ConflictError("simulated_failure", "Group failed.")

    monkeypatch.setattr(DeploymentService, "create_deployment", failing_create)
    rolled = client.post(f"/api/v1/organizations/{org.id}/deployments/{bad['id']}/rollback", headers=headers)
    assert rolled.status_code == 409, rolled.text
    assert rolled.json()["error"]["code"] == "simulated_failure"


def test_rollback_of_a_first_install_is_refused(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _register_device(client, token, name="rb-first", mac="aa:bb:cc:dd:50:10")
    first = _deploy(client, org.id, headers, release, [device["device_id"]])
    _report(client, device, release, health="healthy")
    rolled = client.post(f"/api/v1/organizations/{org.id}/deployments/{first['id']}/rollback", headers=headers)
    assert rolled.status_code == 422, rolled.text
    assert rolled.json()["error"]["code"] == "no_previous_release"


def test_release_of_another_kind_cannot_replace_a_workload(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="rb-kind", mac="aa:bb:cc:dd:50:20")
    container = _docker_web_release(client, org.id, headers, name="web")
    _deploy(client, org.id, headers, container, [device["device_id"]])

    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("docker-compose.yml", "services:\n  web:\n    image: nginx:alpine\n")
    artifact = client.post(
        f"/api/v1/organizations/{org.id}/artifacts",
        headers=headers,
        data={"name": "web-compose", "version": "1.0", "type": "docker_compose"},
        files={"file": ("web.zip", bundle.getvalue(), "application/zip")},
    ).json()
    compose = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "web-compose",
            "version": "1.0.0",
            "kind": "docker_compose",
            "artifact_id": artifact["id"],
            "spec": {"project_name": "web"},
        },
    )
    assert compose.status_code == 201, compose.text

    refused = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": compose.json()["id"], "target": {"device_ids": [device["device_id"]]}},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "workload_kind_mismatch"
    assert "rb-kind (Single container)" in refused.json()["error"]["message"]
    listed = client.get(f"/api/v1/organizations/{org.id}/deployments", headers=headers).json()
    assert listed["total"] == 1  # nothing recorded for the refused Deploy
