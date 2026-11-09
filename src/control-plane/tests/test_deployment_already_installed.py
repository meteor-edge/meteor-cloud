"""Deploy skips devices that already run the release healthily, unless forced."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_deploy_ports import _docker_release
from tests.test_deployments import _register_device, _report, _setup_release


def _deploy(client: TestClient, org, headers: dict, release: dict, device_ids: list[str], **extra):
    response = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": device_ids}, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _software(client: TestClient, org, headers: dict, device_id: str) -> dict:
    rows = client.get(f"/api/v1/organizations/{org.id}/devices/{device_id}/software", headers=headers).json()
    assert len(rows) == 1
    return rows[0]


def _installed_device(client: TestClient, org, headers: dict, token: str, release: dict, *, mac: str) -> dict:
    device = _register_device(client, token, name=f"edge-{mac[-2:]}", mac=mac)
    _deploy(client, org, headers, release, [device["device_id"]])
    _report(client, device, release, health="healthy")
    return device


def test_redeploy_skips_a_healthy_device_and_completes(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:01")
    revision = _software(client, org, headers, device["device_id"])["desired_revision"]

    body = _deploy(client, org, headers, release, [device["device_id"]])

    assert body["status"] == "completed"
    assert body["counts"]["skipped"] == 1
    assert body["devices"][0]["status"] == "skipped"
    assert "Already installed" in body["devices"][0]["last_error"]
    # The agent reinstalls when the revision changes, so it must stay the same.
    assert _software(client, org, headers, device["device_id"])["desired_revision"] == revision


def test_force_reinstalls_a_healthy_device(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:02")
    revision = _software(client, org, headers, device["device_id"])["desired_revision"]

    body = _deploy(client, org, headers, release, [device["device_id"]], force=True)

    assert body["devices"][0]["status"] == "pending"
    assert _software(client, org, headers, device["device_id"])["desired_revision"] == revision + 1


def test_unhealthy_device_is_reinstalled(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:03")
    _report(client, device, release, health="unhealthy", last_error="unit is not active")

    body = _deploy(client, org, headers, release, [device["device_id"]])

    assert body["devices"][0]["status"] == "pending"


def test_device_still_installing_is_not_skipped(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-new", mac="aa:bb:cc:dd:40:04")
    _deploy(client, org, headers, release, [device["device_id"]])

    body = _deploy(client, org, headers, release, [device["device_id"]])

    assert body["devices"][0]["status"] == "pending"


def test_edited_release_spec_is_installed(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:05")
    edited = client.patch(
        f"/api/v1/organizations/{org.id}/releases/{release['id']}",
        headers=headers,
        json={"spec": {"unit_name": "camera-agent.service", "start": False}},
    )
    assert edited.status_code == 200, edited.text

    body = _deploy(client, org, headers, release, [device["device_id"]])

    assert body["devices"][0]["status"] == "pending"
    assert _software(client, org, headers, device["device_id"])["desired_spec"]["start"] is False


def test_other_release_with_the_same_artifact_is_installed(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:06")
    same_artifact = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": release["name"],
            "version": "1.3.0",
            "kind": "systemd",
            "artifact_id": release["artifact_id"],
            "spec": release["spec"],
        },
    )
    assert same_artifact.status_code == 201, same_artifact.text

    body = _deploy(client, org, headers, same_artifact.json(), [device["device_id"]])

    assert body["devices"][0]["status"] == "pending"


def test_phased_rollout_leaves_installed_devices_out_of_its_waves(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    installed = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:07")
    fresh = _register_device(client, token, name="edge-fresh", mac="aa:bb:cc:dd:40:08")

    body = _deploy(
        client,
        org,
        headers,
        release,
        [installed["device_id"], fresh["device_id"]],
        rollout={"strategy": "phased", "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 3600}]},
    )

    assert body["rollout"]["activated_device_ids"] == [fresh["device_id"]]
    statuses = {row["device_id"]: row["status"] for row in body["devices"]}
    assert statuses == {installed["device_id"]: "skipped", fresh["device_id"]: "pending"}


def test_rollback_leaves_devices_that_were_skipped_alone(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _installed_device(client, org, headers, token, release, mac="aa:bb:cc:dd:40:09")
    body = _deploy(client, org, headers, release, [device["device_id"]])

    rolled = client.post(f"/api/v1/organizations/{org.id}/deployments/{body['id']}/rollback", headers=headers)

    # Nothing was installed by that Deploy, so there is nothing to go back from.
    assert rolled.status_code == 422, rolled.text
    assert rolled.json()["error"]["code"] == "no_previous_release"


def test_installed_app_can_be_published_without_a_reinstall(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    release = _docker_release(client, org.id, headers, name="web", container="web", ports=["8080:80"])
    device = _register_device(client, token, name="edge-web", mac="aa:bb:cc:dd:40:0a")
    _deploy(client, org, headers, release, [device["device_id"]])
    heartbeat = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {device['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "web",
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
    revision = _software(client, org, headers, device["device_id"])["desired_revision"]

    body = _deploy(client, org, headers, release, [device["device_id"]], endpoint={"port": 8080})

    assert body["devices"][0]["status"] == "skipped"
    assert _software(client, org, headers, device["device_id"])["desired_revision"] == revision
    listed = client.get(
        f"/api/v1/organizations/{org.id}/endpoints", headers=headers, params={"device_id": device["device_id"]}
    ).json()
    assert len(listed) == 1
    assert listed[0]["port"] == 8080
    assert listed[0]["deployment_id"] == body["id"]


def test_publishing_still_needs_a_device_that_can_run_the_app(client: TestClient, db_session: Session) -> None:
    from app.devices.models import Device

    org, headers, token, _ = _setup_release(client, db_session)
    release = _docker_release(client, org.id, headers, name="web", container="web", ports=["8080:80"])
    device = _register_device(client, token, name="edge-none", mac="aa:bb:cc:dd:40:0b")
    row = db_session.get(Device, device["device_id"])
    row.metadata_ = {"capabilities": {"docker": False}}
    db_session.commit()

    response = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [device["device_id"]]},
            "endpoint": {"port": 8080},
        },
    )

    assert response.status_code == 422, response.text
    err = response.json()["error"]
    assert err["code"] == "endpoint_requires_capable_device"
    assert "docker" in err["message"]
