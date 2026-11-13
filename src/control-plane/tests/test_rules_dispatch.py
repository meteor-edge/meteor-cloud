"""Routes the rule dispatcher and the console use beyond alert records: deliveries,
device commands, platform events, rule tests and evaluation status."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.authorization.roles import get_system_role_id
from app.core.exceptions import ValidationAppError
from app.device_actions.models import DeviceAction
from app.rules.dependencies import get_rules_engine_client
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_rules import INTERNAL, _definition, _device, _org, _rule


def _action(rule: dict, device, transition: str = "firing", **action: Any) -> dict:
    return {
        "idempotency_key": f"{rule['id']}:{device.id}:1:{transition}:a1",
        "rule_id": rule["id"],
        "rule_version": rule["current_version"],
        "rule_name": rule["name"],
        "organization_id": str(device.organization_id),
        "device_id": str(device.id),
        "severity": "critical",
        "event": {"transition": transition, "episode": 1, "at": datetime.now(UTC).isoformat(), "values": {"t": 1}},
        "action": action,
    }


def test_delivery_returns_integration_secret_and_silence(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    other, other_headers = _org(client, db_session, email="other@example.com")
    device = _device(db_session, org)
    integration = client.post(
        f"/api/v1/organizations/{org.id}/notification-integrations",
        headers=headers,
        json={"name": "Ops", "url": "https://hooks.example.com/a", "secret": "x" * 20},
    ).json()
    rule = _rule(client, org, headers)
    body = {
        "integration_id": integration["id"],
        "organization_id": str(org.id),
        "rule_id": rule["id"],
        "device_id": str(device.id),
        "at": datetime.now(UTC).isoformat(),
    }
    got = client.post("/internal/rules/deliveries", headers=INTERNAL, json=body)
    assert got.status_code == 200, got.text
    assert got.json() == {
        "url": "https://hooks.example.com/a",
        "kind": "webhook",
        "secret": "x" * 20,
        "enabled": True,
        "silenced": False,
    }
    ends = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    client.post(f"/api/v1/organizations/{org.id}/alert-silences", headers=headers, json={"ends_at": ends})
    later = {**body, "at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()}
    assert client.post("/internal/rules/deliveries", headers=INTERNAL, json=later).json()["silenced"] is True
    # Another organization's action cannot read this integration.
    foreign = {**body, "organization_id": str(other.id)}
    assert client.post("/internal/rules/deliveries", headers=INTERNAL, json=foreign).status_code == 404
    assert client.post("/internal/rules/deliveries", json=body).status_code == 401
    assert other_headers


def test_commands_run_with_the_authors_current_permissions(client: TestClient, db_session: Session) -> None:
    org, owner_headers = _org(client, db_session)
    device = _device(db_session, org)
    admin = create_user(db_session, email="admin@example.com")
    membership = add_member(db_session, org, admin, OrganizationRole.ADMIN)
    headers = auth_header(client, "admin@example.com")
    definition = _definition(actions=[{"type": "alert"}, {"type": "command", "command": "reboot"}])
    rule = _rule(client, org, headers, definition)
    item = _action(rule, device, type="command", command="reboot")

    sent = client.post("/internal/rules/commands", headers=INTERNAL, json=item)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"
    rows = list(db_session.scalars(select(DeviceAction).where(DeviceAction.device_id == device.id)))
    assert [(r.kind, r.requested_by_user_id) for r in rows] == [("reboot", admin.id)]
    # A redelivery finds the restart already waiting.
    assert client.post("/internal/rules/commands", headers=INTERNAL, json=item).json()["status"] == "skipped"
    audit = db_session.scalar(select(AuditEvent).where(AuditEvent.action == "rule.command"))
    assert audit.actor_type == "rule" and audit.metadata_["command"] == "reboot"

    # The author is demoted: the command is refused (and dead-lettered by the dispatcher).
    db_session.query(DeviceAction).delete()
    db_session.commit()
    membership.role_id = get_system_role_id(db_session, OrganizationRole.VIEWER.value)
    db_session.commit()
    assert client.post("/internal/rules/commands", headers=INTERNAL, json=item).status_code == 403
    assert db_session.scalars(select(DeviceAction)).first() is None
    assert owner_headers


def test_platform_events_are_audited_once(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org)
    rule = _rule(client, org, headers)
    item = _action(rule, device, type="platform_event", params={"kind": "maintenance"})
    for _ in range(2):
        assert client.post("/internal/rules/platform-events", headers=INTERNAL, json=item).status_code == 204
    rows = list(db_session.scalars(select(AuditEvent).where(AuditEvent.action == "rule.event")))
    assert len(rows) == 1 and rows[0].metadata_["params"] == {"kind": "maintenance"}
    item["device_id"] = str(uuid.uuid4())
    assert client.post("/internal/rules/platform-events", headers=INTERNAL, json=item).status_code == 404


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def simulate(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("simulate", body))
        if not body["events"]:
            raise ValidationAppError("invalid_rule_test", "events: at least one")
        return {"steps": [{"phase": "firing"}]}

    def status(self, rule_id: str, *, after: str = "", limit: int = 100) -> dict[str, Any]:
        self.calls.append(("status", (rule_id, after, limit)))
        return {"devices": [{"device_id": "d", "phase": "pending"}]}


def test_rule_test_and_status_are_proxied_to_the_engine(client: TestClient, db_session: Session) -> None:
    fake = FakeEngine()
    client.app.dependency_overrides[get_rules_engine_client] = lambda: fake
    org, headers = _org(client, db_session)
    rule = _rule(client, org, headers)
    base = f"/api/v1/organizations/{org.id}/rules"
    events = [{"time": "2026-11-13T12:00:00Z", "data": {"cpu_percent": 95}}]
    tested = client.post(f"{base}/test", headers=headers, json={"definition": _definition(), "events": events})
    assert tested.status_code == 200, tested.text
    assert tested.json() == {"steps": [{"phase": "firing"}]}
    assert fake.calls[0][1]["events"][0]["data"] == {"cpu_percent": 95}
    bad = client.post(f"{base}/test", headers=headers, json={"definition": _definition(severity="x"), "events": events})
    assert bad.status_code == 422 and len(fake.calls) == 1
    assert (
        client.post(f"{base}/test", headers=headers, json={"definition": _definition(), "events": []}).status_code
        == 422
    )

    status = client.get(f"{base}/{rule['id']}/status", headers=headers, params={"limit": 10}).json()
    assert status["devices"][0]["phase"] == "pending" and status["current_version"] == 1
    assert fake.calls[-1] == ("status", (rule["id"], "", 10))


def test_engine_routes_answer_503_when_not_configured(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    rule = _rule(client, org, headers)
    response = client.get(f"/api/v1/organizations/{org.id}/rules/{rule['id']}/status", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rules_engine_not_configured"
