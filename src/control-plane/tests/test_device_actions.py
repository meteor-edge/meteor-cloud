"""Device actions: who may order them, password handling, agent repair (restart_module, run_diagnostics)."""

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
        "from_version": None,
        "module": None,
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

    assert client.post(f"{url}/reboot", headers=operator).status_code == 403
    assert client.post(f"{url}/shutdown", headers=viewer).status_code == 403
    assert client.post(f"{url}/reboot", headers=admin).status_code == 200
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


def test_update_agent_requires_permission_and_rejects_a_second_order(
    client: TestClient, db_session: Session
) -> None:
    org, headers, agent, url = _device(client, db_session)
    add_member(db_session, org, create_user(db_session, email="operator@example.com"), OrganizationRole.OPERATOR)
    operator = auth_header(client, "operator@example.com")

    assert client.post(f"{url}/update-agent", headers=operator).status_code == 403
    assert client.post(f"{url}/update-agent", headers=headers).status_code == 422

    client.post(
        "/api/v1/agent/heartbeat",
        headers=agent,
        json={"agent_version": "0.3.3"},
    )
    ordered = client.post(f"{url}/update-agent", headers=headers)
    assert ordered.status_code == 200, ordered.text
    [action] = [row for row in ordered.json()["actions"] if row["kind"] == "update_agent"]
    assert action["state"] == "pending"
    assert action["from_version"] == "0.3.3"

    again = client.post(f"{url}/update-agent", headers=headers)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "agent_update_pending"

    [change] = [row for row in _collect(client, agent) if row["kind"] == "update_agent"]
    assert change["from_version"] == "0.3.3"
    # Still in flight (sent): another order is refused.
    assert client.post(f"{url}/update-agent", headers=headers).status_code == 409

    assert _report(client, agent, results=[{"id": action["id"], "state": "done"}]) == {"accepted": 1}
    events = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "device.action.update_agent")).all()
    assert len(events) == 1


def test_sent_update_agent_expires_so_a_lost_response_does_not_block(
    client: TestClient, db_session: Session
) -> None:
    _org, headers, agent, url = _device(client, db_session)
    client.post("/api/v1/agent/heartbeat", headers=agent, json={"agent_version": "0.3.3"})
    ordered = client.post(f"{url}/update-agent", headers=headers)
    action_id = next(row["id"] for row in ordered.json()["actions"] if row["kind"] == "update_agent")
    assert _collect(client, agent)
    row = db_session.get(DeviceAction, action_id)
    assert row is not None and row.state == STATE_SENT
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert client.post(f"{url}/update-agent", headers=headers).status_code == 200


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


GO_CAPABILITIES = {
    "systemd": True,
    "docker": True,
    "actions": ["reboot", "shutdown", "set_password", "add_user", "update_agent", "restart_module", "run_diagnostics"],
    "deploy_kinds": ["systemd", "docker", "docker_compose"],
    "modules": {
        "heartbeat": {"state": "running", "restarts": 0},
        "deploy": {"state": "parked", "restarts": 5, "last_error": "panic: boom"},
    },
}


def test_heartbeat_stores_module_health_cleaned(client: TestClient, db_session: Session) -> None:
    org, headers, agent, url = _device(client, db_session)
    caps = {
        **GO_CAPABILITIES,
        "gpio": {"pins": 40},
        "modules": {
            **GO_CAPABILITIES["modules"],
            "weird": {"state": "exploded"},
            "metrics": {"state": "failed", "restarts": -1, "last_error": "x" * 900},
        },
        "deploy_kinds": "docker",
    }
    response = client.post("/api/v1/agent/heartbeat", headers=agent, json={"capabilities": caps})
    assert response.status_code == 200, response.text

    device = client.get(url.removesuffix("/actions"), headers=headers).json()
    stored = device["metadata"]["capabilities"]
    assert stored["gpio"] == {"pins": 40}  # unknown keys kept as sent
    assert "deploy_kinds" not in stored  # wrong type dropped
    assert stored["modules"]["deploy"] == {"state": "parked", "restarts": 5, "last_error": "panic: boom"}
    assert "weird" not in stored["modules"]
    assert stored["modules"]["metrics"] == {"state": "failed", "last_error": "x" * 500}
    assert device["metadata"]["capabilities_reported_at"]

    # A progress-only heartbeat (no capabilities) keeps the last module health.
    client.post("/api/v1/agent/heartbeat", headers=agent, json={})
    device = client.get(url.removesuffix("/actions"), headers=headers).json()
    assert device["metadata"]["capabilities"]["modules"]["deploy"]["state"] == "parked"


def test_restart_module_and_diagnostics_round_trip(client: TestClient, db_session: Session) -> None:
    org, headers, agent, url = _device(client, db_session)
    add_member(db_session, org, create_user(db_session, email="operator@example.com"), OrganizationRole.OPERATOR)
    operator = auth_header(client, "operator@example.com")
    client.post("/api/v1/agent/heartbeat", headers=agent, json={"capabilities": GO_CAPABILITIES})

    assert client.post(f"{url}/restart-module", headers=operator, json={"module": "deploy"}).status_code == 403
    assert client.post(f"{url}/restart-module", headers=headers, json={"module": "-rf"}).status_code == 422
    unknown = client.post(f"{url}/restart-module", headers=headers, json={"module": "printer"})
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "module_unknown"

    ordered = client.post(f"{url}/restart-module", headers=headers, json={"module": "deploy"})
    assert ordered.status_code == 200, ordered.text
    [restart] = [row for row in ordered.json()["actions"] if row["kind"] == "restart_module"]
    assert restart["module"] == "deploy"
    again = client.post(f"{url}/restart-module", headers=headers, json={"module": "deploy"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "repair_action_pending"

    diagnose = client.post(f"{url}/run-diagnostics", headers=headers)
    assert diagnose.status_code == 200, diagnose.text
    [diagnostics] = [row for row in diagnose.json()["actions"] if row["kind"] == "run_diagnostics"]

    collected = {row["kind"]: row for row in _collect(client, agent)}
    assert collected["restart_module"]["module"] == "deploy"
    assert collected["run_diagnostics"]["module"] is None

    report = "server: ok\nmodules: deploy parked\n"
    assert _report(
        client,
        agent,
        results=[
            {"id": restart["id"], "state": "done", "output": "ignored for this kind"},
            {"id": diagnostics["id"], "state": "done", "output": report},
        ],
    ) == {"accepted": 2}
    rows = {row["kind"]: row for row in client.get(url, headers=headers).json()["actions"]}
    assert rows["restart_module"]["state"] == "done"
    assert rows["restart_module"]["output"] is None
    assert rows["run_diagnostics"]["output"] == report
    assert client.get(url, headers=operator).status_code == 403
    events = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "device.action.restart_module")).all()
    assert [event.metadata_["module"] for event in events] == ["deploy"]


def test_actions_the_agent_does_not_advertise_are_refused(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    # An agent from before the repair actions.
    older = {**GO_CAPABILITIES, "actions": ["reboot", "shutdown", "set_password", "add_user", "update_agent"]}
    client.post("/api/v1/agent/heartbeat", headers=agent, json={"capabilities": older})
    refused = client.post(f"{url}/run-diagnostics", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "action_unsupported"
    assert client.post(f"{url}/reboot", headers=headers).status_code == 200


def test_agent_without_action_list_keeps_every_kind(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    client.post("/api/v1/agent/heartbeat", headers=agent, json={"capabilities": {"docker": True}})
    assert client.post(f"{url}/run-diagnostics", headers=headers).status_code == 200
    assert client.post(f"{url}/restart-module", headers=headers, json={"module": "deploy"}).status_code == 200
