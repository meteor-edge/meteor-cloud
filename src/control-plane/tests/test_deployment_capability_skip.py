"""Deploy skips devices that reported they lack the release kind's runtime."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.models import Device
from tests.conftest import auth_header, create_org_with_owner, create_user

UNIT = b"[Unit]\nDescription=Camera\n[Service]\nExecStart=/bin/true\n"


def test_systemd_deploy_skips_device_without_systemd_capability(
    client: TestClient, db_session: Session
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap"},
    ).json()["token"]

    ok = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"], "name": "with-systemd"},
    ).json()
    no = client.post(
        "/api/v1/agent/register",
        json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:02"], "name": "no-systemd"},
    ).json()

    device_no = db_session.get(Device, uuid.UUID(no["device_id"]))
    assert device_no is not None
    device_no.metadata_ = {"capabilities": {"systemd": False, "docker": False}}
    db_session.commit()

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

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [ok["device_id"], no["device_id"]]},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    by_device = {row["device_id"]: row for row in body["devices"]}
    assert by_device[ok["device_id"]]["status"] == "pending"
    assert by_device[no["device_id"]]["status"] == "skipped"
    assert "systemd" in (by_device[no["device_id"]]["last_error"] or "")

    # Desired software only on the compatible device.
    soft_ok = client.get(
        f"/api/v1/organizations/{org.id}/devices/{ok['device_id']}/software",
        headers=headers,
    ).json()
    soft_no = client.get(
        f"/api/v1/organizations/{org.id}/devices/{no['device_id']}/software",
        headers=headers,
    ).json()
    assert len(soft_ok) == 1
    assert soft_no == []
