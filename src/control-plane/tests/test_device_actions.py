"""Restart, shut down and SSH-user actions: who may order them, and password handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.device_actions.models import STATE_PENDING, STATE_SENT, DeviceAction
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_deployments import _register_device, _setup_release

PASSWORD = "correct-horse-battery"


def _device(client: TestClient, db_session: Session):
    org, headers, token, _release = _setup_release(client, db_session)
    registered = _register_device(client, token, name="kiosk", mac="aa:bb:cc:dd:20:01")
    agent = {"Authorization": f"Bearer {registered['device_token']}"}
    url = f"/api/v1/organizations/{org.id}/devices/{registered['device_id']}/actions"
    return org, headers, agent, url


def _heartbeat(client: TestClient, agent: dict) -> dict:
    response = client.post("/api/v1/agent/heartbeat", headers=agent, json={})
    assert response.status_code == 200, response.text
    return response.json()


def _collect(client: TestClient, agent: dict) -> list[dict]:
    response = client.get("/api/v1/agent/actions", headers=agent)
    assert response.status_code == 200, response.text
    return response.json()["actions"]


def _report(client: TestClient, agent: dict, **body) -> dict:
    response = client.post("/api/v1/agent/actions/report", headers=agent, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_restart_is_handed_out_once_and_never_shows_a_password(client: TestClient, db_session: Session) -> None:
    org, headers, agent, url = _device(client, db_session)
    assert _heartbeat(client, agent)["actions_pending"] is False

    ordered = client.post(f"{url}/reboot", headers=headers)
    assert ordered.status_code == 200, ordered.text
    [action] = ordered.json()["actions"]
    assert action["kind"] == "reboot"
    assert action["state"] == "pending"
    assert "password" not in action
    assert _heartbeat(client, agent)["actions_pending"] is True

    [change] = _collect(client, agent)
    assert change == {
        "id": action["id"],
        "kind": "reboot",
        "username": None,
        "password": None,
        "sudo": False,
        "authorized_key": None,
    }
    # Already handed out: a second collect is empty, and the heartbeat flag clears.
    assert _collect(client, agent) == []
    assert _heartbeat(client, agent)["actions_pending"] is False

    assert _report(client, agent, results=[{"id": action["id"], "state": "done"}]) == {"accepted": 1}
    [done] = client.get(url, headers=headers).json()["actions"]
    assert done["state"] == "done"

    events = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "device.action.reboot")).all()
    assert len(events) == 1
    assert "password" not in str(events[0].metadata_)


def test_password_change_is_erased_when_collected(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    response = client.post(f"{url}/set-password", headers=headers, json={"username": "pi", "password": PASSWORD})
    assert response.status_code == 200, response.text
    [action] = response.json()["actions"]
    assert action["kind"] == "set_password"
    assert action["username"] == "pi"
    assert PASSWORD not in response.text

    [change] = _collect(client, agent)
    assert change["password"] == PASSWORD
    row = db_session.get(DeviceAction, action["id"])
    assert row is not None
    assert row.state == STATE_SENT
    assert row.password_sealed is None

    assert _report(client, agent, results=[{"id": action["id"], "state": "done"}]) == {"accepted": 1}


def test_add_user_and_permissions(client: TestClient, db_session: Session) -> None:
    org, headers, agent, url = _device(client, db_session)
    add_member(db_session, org, create_user(db_session, email="admin@example.com"), OrganizationRole.ADMIN)
    add_member(db_session, org, create_user(db_session, email="operator@example.com"), OrganizationRole.OPERATOR)
    add_member(db_session, org, create_user(db_session, email="viewer@example.com"), OrganizationRole.VIEWER)

    admin = auth_header(client, "admin@example.com")
    operator = auth_header(client, "operator@example.com")
    viewer = auth_header(client, "viewer@example.com")

    assert client.post(f"{url}/reboot", headers=operator).status_code == 200
    assert client.post(f"{url}/shutdown", headers=viewer).status_code == 403
    assert (
        client.post(f"{url}/set-password", headers=operator, json={"username": "pi", "password": PASSWORD}).status_code
        == 403
    )
    created = client.post(
        f"{url}/add-user",
        headers=admin,
        json={
            "username": "ops",
            "password": PASSWORD,
            "sudo": True,
            "authorized_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJustATestKey you@laptop",
        },
    )
    assert created.status_code == 200, created.text
    [action] = [row for row in created.json()["actions"] if row["kind"] == "add_user"]
    assert action["sudo"] is True

    [change] = [row for row in _collect(client, agent) if row["kind"] == "add_user"]
    assert change["password"] == PASSWORD
    assert change["authorized_key"].startswith("ssh-ed25519 ")
    assert PASSWORD not in client.get(url, headers=headers).text


def test_expired_action_is_cancelled(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    client.post(f"{url}/reboot", headers=headers)
    row = db_session.scalars(select(DeviceAction).where(DeviceAction.state == STATE_PENDING)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert _heartbeat(client, agent)["actions_pending"] is False
    assert _collect(client, agent) == []
    [failed] = client.get(url, headers=headers).json()["actions"]
    assert failed["state"] == "failed"
    assert "did not collect" in failed["last_error"]
