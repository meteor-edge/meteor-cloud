"""Rules API, alert records, silences, integrations and the engine-facing internal routes."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.config import get_settings
from app.core.secretbox import unseal
from app.devices.models import Device, DeviceGroup
from app.rules.definition import RuleDefinitionError, validate_definition
from app.rules.models import NotificationIntegration
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user

INTERNAL = {"X-Data-Plane-Internal-Token": get_settings().data_plane_internal_token}
EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "rules-v1.examples.json"


def _definition(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "name": "High CPU",
        "severity": "critical",
        "scope": {"type": "organization"},
        "condition": {
            "type": "duration",
            "duration": "5m",
            "condition": {"type": "comparison", "field": "cpu_percent", "operator": "gt", "value": 90},
        },
    }
    base.update(overrides)
    return base


def _org(client: TestClient, db_session: Session, email: str = "owner@example.com"):
    owner = create_user(db_session, email=email)
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, email)


def _device(db_session: Session, org, name: str = "edge-1", **fields) -> Device:
    device = Device(organization_id=org.id, name=name, **fields)
    db_session.add(device)
    db_session.commit()
    return device


def _rule(client: TestClient, org, headers, definition: dict | None = None) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org.id}/rules", headers=headers, json={"definition": definition or _definition()}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _transition(rule: dict, device: Device, transition: str, episode: int, **event) -> dict:
    return {
        "idempotency_key": f"{rule['id']}:{device.id}:{episode}:{transition}:a0",
        "rule_id": rule["id"],
        "rule_version": rule["current_version"],
        "rule_name": rule["name"],
        "organization_id": str(device.organization_id),
        "device_id": str(device.id),
        "severity": "critical",
        "event": {
            "transition": transition,
            "episode": episode,
            "at": datetime.now(UTC).isoformat(),
            "values": {"cpu_percent": 97.5},
            **event,
        },
        "action": {"type": "alert"},
    }


def _send(client: TestClient, *items: dict) -> list[dict]:
    response = client.post("/internal/rules/alert-transitions", headers=INTERNAL, json={"items": list(items)})
    assert response.status_code == 200, response.text
    return response.json()["results"]


# ---- definition validator (mirror of the Go validator) -------------------


@pytest.mark.parametrize("index", range(len(json.loads(EXAMPLES.read_text()))))
def test_contract_examples_are_valid(index: int) -> None:
    example = json.loads(EXAMPLES.read_text())[index]
    validate_definition(example, allow_platform=True)


INVALID = [
    ({"schema_version": 2}, "schema_version"),
    ({"nme": "typo"}, ""),
    ({"severity": "fatal"}, "severity"),
    ({"scope": {"type": "device"}}, "scope.device_ids"),
    ({"scope": {"type": "platform"}}, "scope.type"),
    ({"scope": {"type": "organization", "group_id": "g"}}, "scope.group_id"),
    ({"condition": {"type": "comparison", "field": "a", "operator": "gt", "value": "x"}}, "condition.value"),
    ({"condition": {"type": "comparison", "field": "a", "operator": "between", "min": 5, "max": 1}}, "condition"),
    ({"condition": {"type": "comparison", "field": "a..b", "operator": "exists"}}, "condition.field"),
    ({"condition": {"type": "script", "source": "rm -rf /"}}, "condition"),
    (
        {
            "condition": {
                "type": "duration",
                "duration": "48h",
                "condition": {"type": "comparison", "field": "a", "operator": "exists"},
            }
        },
        "condition.duration",
    ),
    (
        {
            "condition": {
                "type": "aggregate",
                "function": "median",
                "field": "a",
                "window": "1m",
                "operator": "gt",
                "value": 1,
            }
        },
        "condition.function",
    ),
    ({"cooldown": "soon"}, "cooldown"),
    ({"actions": [{"type": "webhook"}]}, "actions[0].integration_id"),
    ({"actions": [{"type": "alert"}, {"type": "alert"}]}, "actions"),
]


@pytest.mark.parametrize(("overrides", "path"), INVALID)
def test_invalid_definitions_are_rejected(overrides: dict, path: str) -> None:
    with pytest.raises(RuleDefinitionError) as info:
        validate_definition(_definition(**overrides))
    assert any(p.path.startswith(path) for p in info.value.problems), info.value.problems


def test_nesting_limits() -> None:
    node: dict = {"type": "comparison", "field": "a", "operator": "exists"}
    for _ in range(9):
        node = {"type": "not", "condition": node}
    with pytest.raises(RuleDefinitionError):
        validate_definition(_definition(condition=node))


# ---- rules API -----------------------------------------------------------


def test_rule_crud_versions_and_audit(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    base = f"/api/v1/organizations/{org.id}/rules"
    rule = _rule(client, org, headers)
    assert rule["current_version"] == 1 and rule["enabled"] and rule["severity"] == "critical"

    renamed = _definition(name="Very high CPU")
    updated = client.put(f"{base}/{rule['id']}", headers=headers, json={"definition": renamed}).json()
    assert updated["current_version"] == 2 and updated["name"] == "Very high CPU"
    versions = client.get(f"{base}/{rule['id']}/versions", headers=headers).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["definition"]["name"] == "High CPU"

    disabled = client.post(f"{base}/{rule['id']}/disable", headers=headers).json()
    assert disabled["enabled"] is False and disabled["current_version"] == 2
    assert client.get(base, headers=headers, params={"enabled": "true"}).json() == []
    client.post(f"{base}/{rule['id']}/enable", headers=headers)

    assert client.delete(f"{base}/{rule['id']}", headers=headers).status_code == 204
    assert client.get(f"{base}/{rule['id']}", headers=headers).status_code == 404
    assert client.get(base, headers=headers).json() == []

    actions = set(db_session.scalars(select(AuditEvent.action).where(AuditEvent.resource_type == "rule")))
    assert {"rule.create", "rule.update", "rule.disable", "rule.enable", "rule.delete"} <= actions
    update = db_session.scalar(select(AuditEvent).where(AuditEvent.action == "rule.update"))
    assert update.metadata_["state_reset"] is False


def test_invalid_rule_is_rejected_with_problems(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    base = f"/api/v1/organizations/{org.id}/rules"
    bad = _definition(severity="fatal", scope={"type": "platform"})
    response = client.post(base, headers=headers, json={"definition": bad})
    assert response.status_code == 422
    assert "severity" in response.json()["error"]["message"]

    result = client.post(f"{base}/validate", headers=headers, json={"definition": bad}).json()
    assert result["valid"] is False
    assert {p["path"] for p in result["problems"]} >= {"severity", "scope.type"}
    ok = client.post(f"{base}/validate", headers=headers, json={"definition": _definition()}).json()
    assert ok["valid"] is True and ok["condition_hash"]


def test_scope_references_must_belong_to_the_organization(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    other, _ = _org(client, db_session, email="other@example.com")
    foreign = _device(db_session, other, name="theirs")
    foreign_group = DeviceGroup(organization_id=other.id, name="G", slug="g")
    db_session.add(foreign_group)
    db_session.commit()
    base = f"/api/v1/organizations/{org.id}/rules"
    for scope in (
        {"type": "device", "device_ids": [str(foreign.id)]},
        {"type": "group", "group_id": str(foreign_group.id)},
        {"type": "device_type", "device_type_id": str(uuid.uuid4())},
    ):
        response = client.post(base, headers=headers, json={"definition": _definition(scope=scope)})
        assert response.status_code == 422, scope
    hook = _definition(actions=[{"type": "webhook", "integration_id": str(uuid.uuid4())}])
    assert client.post(base, headers=headers, json={"definition": hook}).status_code == 422


def test_preview_devices_applies_scope_and_labels(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    other, _ = _org(client, db_session, email="other@example.com")
    prod = _device(db_session, org, name="a-prod", labels={"env": "production"})
    _device(db_session, org, name="b-dev", labels={"env": "dev"})
    _device(db_session, org, name="c-off", labels={"env": "production"}, is_enabled=False)
    _device(db_session, other, name="theirs", labels={"env": "production"})
    scope = {"type": "organization", "labels": {"env": "production"}}
    preview = client.post(
        f"/api/v1/organizations/{org.id}/rules/preview-devices",
        headers=headers,
        json={"definition": {"scope": scope}},
    ).json()
    assert preview["total"] == 1 and preview["devices"][0]["id"] == str(prod.id)


def test_rule_permissions(client: TestClient, db_session: Session) -> None:
    org, owner_headers = _org(client, db_session)
    rule = _rule(client, org, owner_headers)
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    headers = auth_header(client, "viewer@example.com")
    base = f"/api/v1/organizations/{org.id}/rules"
    assert client.get(base, headers=headers).status_code == 200
    assert client.post(base, headers=headers, json={"definition": _definition()}).status_code == 403
    assert client.post(f"{base}/{rule['id']}/disable", headers=headers).status_code == 403

    outsider = create_user(db_session, email="outsider@example.com")
    assert outsider
    assert client.get(base, headers=auth_header(client, "outsider@example.com")).status_code == 404


def test_command_actions_need_the_command_permission(client: TestClient, db_session: Session) -> None:
    org, _ = _org(client, db_session)
    developer = create_user(db_session, email="dev@example.com")
    add_member(db_session, org, developer, OrganizationRole.DEVELOPER)
    headers = auth_header(client, "dev@example.com")
    base = f"/api/v1/organizations/{org.id}/rules"
    reboot = _definition(actions=[{"type": "alert"}, {"type": "command", "command": "reboot"}])
    assert client.post(base, headers=headers, json={"definition": reboot}).status_code == 403
    restart_app = _definition(actions=[{"type": "command", "command": "restart"}])
    assert client.post(base, headers=headers, json={"definition": restart_app}).status_code == 201


# ---- engine-facing -------------------------------------------------------


def test_snapshot_lists_enabled_rules_and_devices_with_etag(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org, labels={"env": "production", "floor": 2})
    rule = _rule(client, org, headers, _definition(enabled=False))
    disabled = client.get("/internal/rules/snapshot", headers=INTERNAL).json()
    assert disabled["rules"] == []
    client.post(f"/api/v1/organizations/{org.id}/rules/{rule['id']}/enable", headers=headers)

    response = client.get("/internal/rules/snapshot", headers=INTERNAL)
    snap = response.json()
    assert [r["id"] for r in snap["rules"]] == [rule["id"]]
    assert snap["rules"][0]["definition"]["enabled"] is True
    assert snap["devices"] == [
        {
            "id": str(device.id),
            "organization_id": str(org.id),
            "group_id": None,
            "device_type_id": None,
            "labels": {"env": "production", "floor": "2"},
        }
    ]
    etag = response.headers["ETag"]
    assert etag == f'"{snap["version"]}"'
    again = client.get("/internal/rules/snapshot", headers={**INTERNAL, "If-None-Match": etag})
    assert again.status_code == 304
    assert client.get("/internal/rules/snapshot").status_code == 401
    assert client.get("/internal/rules/snapshot", headers={"X-Data-Plane-Internal-Token": "x"}).status_code == 401


def test_alert_lifecycle_is_idempotent(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org)
    rule = _rule(client, org, headers)
    fire = _transition(rule, device, "firing", 1)

    [first] = _send(client, fire)
    assert first["status"] == "applied"
    [replay] = _send(client, fire)
    assert replay == {**first, "status": "duplicate"}

    alerts_url = f"/api/v1/organizations/{org.id}/alerts"
    [alert] = client.get(alerts_url, headers=headers, params={"active": "true"}).json()
    assert alert["status"] == "firing" and alert["episode"] == 1 and alert["trigger_values"] == {"cpu_percent": 97.5}

    acked = client.post(f"{alerts_url}/{alert['id']}/acknowledge", headers=headers).json()
    assert acked["status"] == "acknowledged" and acked["acknowledged_by_user_id"]
    assert client.post(f"{alerts_url}/{alert['id']}/acknowledge", headers=headers).status_code == 200

    resolve = _transition(rule, device, "resolved", 1, reason="condition_cleared")
    assert _send(client, resolve)[0]["status"] == "applied"
    assert _send(client, resolve)[0]["status"] == "duplicate"
    detail = client.get(f"{alerts_url}/{alert['id']}", headers=headers).json()
    assert detail["status"] == "resolved" and detail["resolve_reason"] == "condition_cleared"
    assert [e["transition"] for e in detail["events"]] == ["firing", "acknowledged", "resolved"]
    assert client.post(f"{alerts_url}/{alert['id']}/acknowledge", headers=headers).status_code == 409

    # A new episode opens a new alert; a lost resolve of the old one is closed as superseded.
    _send(client, _transition(rule, device, "firing", 2))
    _send(client, _transition(rule, device, "firing", 3))
    rows = client.get(f"/api/v1/organizations/{org.id}/rules/{rule['id']}/alerts", headers=headers).json()
    assert [(a["episode"], a["status"]) for a in rows] == [(3, "firing"), (2, "resolved"), (1, "resolved")]
    assert rows[1]["resolve_reason"] == "superseded"

    # Keyset paging walks every row once.
    page = client.get(alerts_url, headers=headers, params={"limit": 2}).json()
    rest = client.get(
        alerts_url,
        headers=headers,
        params={"limit": 2, "before": page[-1]["fired_at"], "before_id": page[-1]["id"]},
    ).json()
    assert [a["episode"] for a in page + rest] == [3, 2, 1]


def test_transitions_cannot_cross_tenants(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    other, _ = _org(client, db_session, email="other@example.com")
    theirs = _device(db_session, other)
    rule = _rule(client, org, headers)
    item = _transition(rule, theirs, "firing", 1)
    assert _send(client, item)[0]["status"] == "ignored"
    item["organization_id"] = str(org.id)
    assert _send(client, item)[0]["status"] == "ignored"
    assert client.get(f"/api/v1/organizations/{org.id}/alerts", headers=headers).json() == []


def test_silences_mark_alerts(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org)
    rule = _rule(client, org, headers)
    _send(client, _transition(rule, device, "firing", 1))
    silences = f"/api/v1/organizations/{org.id}/alert-silences"
    ends = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    silence = client.post(silences, headers=headers, json={"rule_id": rule["id"], "ends_at": ends})
    assert silence.status_code == 201, silence.text
    [alert] = client.get(f"/api/v1/organizations/{org.id}/alerts", headers=headers).json()
    assert alert["silenced"] is True

    other = _device(db_session, org, name="edge-2")
    assert _send(client, _transition(rule, other, "firing", 1))[0]["silenced"] is True

    assert client.delete(f"{silences}/{silence.json()['id']}", headers=headers).status_code == 204
    assert all(not a["silenced"] for a in client.get(f"/api/v1/organizations/{org.id}/alerts", headers=headers).json())
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert client.post(silences, headers=headers, json={"ends_at": past}).status_code == 422


# ---- integrations --------------------------------------------------------


def test_integrations_seal_secrets_and_block_delete_in_use(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    base = f"/api/v1/organizations/{org.id}/notification-integrations"
    created = client.post(
        base,
        headers=headers,
        json={"name": "Ops hook", "url": "https://hooks.example.com/x", "secret": "s" * 24},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_secret"] is True and "secret" not in body
    row = db_session.get(NotificationIntegration, uuid.UUID(body["id"]))
    assert unseal(get_settings(), row.secret_sealed, context=f"integration:{row.id}") == "s" * 24
    assert client.post(base, headers=headers, json={"name": "Ops hook", "url": "https://a.example"}).status_code == 409

    cleared = client.patch(f"{base}/{body['id']}", headers=headers, json={"secret": ""}).json()
    assert cleared["has_secret"] is False

    definition = _definition(actions=[{"type": "alert"}, {"type": "webhook", "integration_id": body["id"]}])
    _rule(client, org, headers, copy.deepcopy(definition))
    assert client.delete(f"{base}/{body['id']}", headers=headers).status_code == 409

    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    viewer_headers = auth_header(client, "viewer@example.com")
    assert client.get(base, headers=viewer_headers).status_code == 200
    assert client.post(base, headers=viewer_headers, json={"name": "x", "url": "https://x.example"}).status_code == 403
