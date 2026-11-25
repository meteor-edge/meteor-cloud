"""Rules API, alert records, silences, integrations and the engine-facing internal routes."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.config import get_settings
from app.core.secretbox import unseal
from app.devices.models import Device, DeviceGroup
from app.rules.definition import RuleDefinitionError, condition_hash, parse_duration, validate_definition
from app.rules.models import AlertSilence, NotificationIntegration
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user

INTERNAL = {"X-Data-Plane-Internal-Token": get_settings().data_plane_internal_token}
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
EXAMPLES = CONTRACTS / "rules-v1.examples.json"


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


_AGGREGATE = {"type": "aggregate", "function": "avg", "field": "a", "window": "1m"}
_COMPARISON = {"type": "comparison", "field": "a"}


# The same cases as the Go validator's TestStrictOperandsAndKeys.
@pytest.mark.parametrize(
    ("valid", "overrides"),
    [
        (True, {"condition": {**_AGGREGATE, "operator": "between", "min": 1, "max": 2}}),
        (True, {"condition": {**_COMPARISON, "operator": "in", "values": [1]}}),
        (True, {"condition": {**_COMPARISON, "operator": "exists"}}),
        (True, {"alerting": {"mode": "per_event"}}),
        (True, {"alerting": {"mode": "per_event", "max_active": 2}}),
        (True, {"actions": [{"type": "platform_event", "on": ["firing", "resolved"]}]}),
        (False, {"condition": {**_AGGREGATE, "operator": "gt", "value": None}}),
        (False, {"condition": {**_AGGREGATE, "operator": "gt", "value": 1, "min": 5}}),
        (False, {"condition": {**_COMPARISON, "operator": "in", "values": [1], "value": 2}}),
        (False, {"condition": {**_COMPARISON, "operator": "exists", "value": None}}),
        (False, {"condition": {**_COMPARISON, "operator": "between", "min": 1, "max": 2, "value": 1}}),
        (False, {"condition": {**_COMPARISON, "operator": "gt", "value": 1, "values": []}}),
        (False, {"condition": {**_COMPARISON, "operator": "gt", "value": 1, "window": "0s"}}),
        (False, {"condition": {**_COMPARISON, "operator": ["gt"], "value": 1}}),
        (False, {"condition": {"type": "absence", "duration": "5m", "streams": ["a" * 64]}}),
        (False, {"alerting": {"mode": "per_event", "max_active": 0}}),
        (False, {"alerting": {"mode": "single", "max_active": 2}}),
        (False, {"alerting": {"max_active": 1}}),
        (False, {"actions": [{"type": "platform_event", "on": ["firing", "firing"]}]}),
        (False, {"actions": [{"type": "platform_event", "on": ["firing", "resolved", "firing"]}]}),
    ],
)
def test_strict_operands_and_keys(valid: bool, overrides: dict) -> None:
    definition = {**_definition(), **overrides}
    if valid:
        validate_definition(definition)
    else:
        with pytest.raises(RuleDefinitionError):
            validate_definition(definition)


def test_condition_hash_uses_effective_alerting() -> None:
    def digest(**overrides) -> str:
        return condition_hash({**_definition(), **overrides})

    per_event = digest(alerting={"mode": "per_event"})
    assert digest() == digest(alerting={"mode": "single"})
    assert per_event == digest(alerting={"mode": "per_event", "max_active": 10})
    assert len({digest(), per_event, digest(alerting={"mode": "per_event", "max_active": 3})}) == 3


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
    unsupported = _definition(actions=[{"type": "command", "command": "restart"}])
    response = client.post(f"{base}/validate", headers=headers, json={"definition": unsupported}).json()
    assert response["problems"] == [{"path": "actions[0].command", "message": "must be one of: reboot, shutdown"}]


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


# ---- review of #43 -------------------------------------------------------


def test_schema_agrees_with_the_shared_examples() -> None:
    schema = Draft202012Validator(json.loads((CONTRACTS / "rules-v1.schema.json").read_text()))
    for example in json.loads(EXAMPLES.read_text()):
        assert schema.is_valid(example), example
    for case in json.loads((CONTRACTS / "rules-v1.invalid.json").read_text()):
        assert not schema.is_valid(case["rule"]), case["reason"]
        with pytest.raises(RuleDefinitionError):
            validate_definition(case["rule"])


def _comparison(**fields) -> dict:
    return {"condition": {"type": "comparison", "field": "a", **fields}}


@pytest.mark.parametrize(
    "overrides",
    [
        _comparison(operator="gt", value=10**310),
        _comparison(operator="in", values=[1, 10**310]),
        _comparison(operator="between", min=0, max=10**310),
        _comparison(operator="eq", value=float("inf")),
        _comparison(operator="exists") | {"cooldown": "1s\n"},
        _comparison(operator="exists") | {"cooldown": "1s "},
        _comparison(operator="exists") | {"cooldown": "٣s"},
        _comparison(operator="exists") | {"source": {"streams": ["app.x\n"]}},
        {"condition": {"type": "comparison", "field": "cpu\n", "operator": "exists"}},
        {"scope": {"type": "group", "group_id": "g\n"}},
        {"schema_version": 1.0},
        {"actions": [{"type": "platform_event", "params": {"x": float("nan")}}]},
        {"actions": [{"type": "platform_event", "params": {"x": "a" * 4089}}]},
    ],
)
def test_what_the_engine_cannot_load_is_refused(overrides: dict) -> None:
    with pytest.raises(RuleDefinitionError):
        validate_definition(_definition(**overrides))


def test_boundaries_stay_valid() -> None:
    assert parse_duration("1h1.5s") == 3601.5 and parse_duration("0s") == 0 and parse_duration("0") is None
    validate_definition(_definition(**_comparison(operator="eq", value=True), cooldown="0s"))
    validate_definition(_definition(actions=[{"type": "platform_event", "params": {"x": "a" * 4088}}]))
    validate_definition(_definition(name="é" * 200))


def test_a_deep_resolve_tree_is_a_problem_not_a_crash() -> None:
    node: dict = {"type": "absence", "duration": "1m"}
    for _ in range(5000):
        node = {"type": "not", "condition": node}
    with pytest.raises(RuleDefinitionError, match="nested deeper"):
        validate_definition(_definition(resolve=node))


def test_condition_hash_uses_effective_sources() -> None:
    base = condition_hash(_definition())
    assert condition_hash(_definition(source={"streams": ["meteor.device.metrics"]})) == base
    assert condition_hash(_definition(source={"streams": ["app.x"]})) != base


def test_scope_ids_are_stored_in_canonical_form(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    group = DeviceGroup(organization_id=org.id, name="g", slug="g")
    db_session.add(group)
    db_session.commit()
    device = _device(db_session, org, device_group_id=group.id)
    scope = {"type": "group", "group_id": str(group.id).upper()}
    rule = _rule(client, org, headers, _definition(scope=scope))
    assert rule["definition"]["scope"]["group_id"] == str(group.id)
    base = f"/api/v1/organizations/{org.id}/rules"
    preview = client.post(f"{base}/preview-devices", headers=headers, json={"definition": {"scope": scope}})
    assert preview.json()["total"] == 1
    rule = _rule(client, org, headers, _definition(scope={"type": "device", "device_ids": [str(device.id).upper()]}))
    assert rule["definition"]["scope"]["device_ids"] == [str(device.id)]
    for bad in ({"type": "organization", "labels": ["env"]}, {"type": "device", "device_ids": 5}, None):
        response = client.post(f"{base}/preview-devices", headers=headers, json={"definition": {"scope": bad}})
        assert response.status_code == 422, response.text


def test_resolve_closes_every_alert_of_the_episode(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org)
    rule = _rule(client, org, headers)
    further = _transition(rule, device, "firing", 1, fingerprint="e2") | {"idempotency_key": "further"}
    _send(client, _transition(rule, device, "firing", 1), further)
    resolved = _transition(rule, device, "resolved", 1)
    assert _send(client, resolved)[0]["status"] == "applied"
    alerts = client.get(f"/api/v1/organizations/{org.id}/alerts", headers=headers).json()
    assert [a["status"] for a in alerts] == ["resolved", "resolved"]
    assert _send(client, resolved)[0]["status"] == "duplicate"


def test_silenced_flag_follows_the_clock(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org)
    rule = _rule(client, org, headers)
    _send(client, _transition(rule, device, "firing", 1))
    base = f"/api/v1/organizations/{org.id}"
    end = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = client.post(f"{base}/alert-silences", headers=headers, json={"ends_at": end}).json()
    assert client.get(f"{base}/alerts", headers=headers).json()[0]["silenced"] is True
    silence = db_session.get(AlertSilence, uuid.UUID(created["id"]))
    silence.starts_at = datetime.now(UTC) - timedelta(hours=2)
    silence.ends_at = datetime.now(UTC) - timedelta(hours=1)  # ended without any write to the alert
    db_session.commit()
    alert = client.get(f"{base}/alerts", headers=headers).json()[0]
    assert alert["silenced"] is False
    assert client.get(f"{base}/alerts/{alert['id']}", headers=headers).json()["silenced"] is False


def test_silence_times_need_a_timezone(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/alert-silences",
        headers=headers,
        json={"starts_at": "2036-12-01T10:00:00", "ends_at": "2036-12-01T11:00:00Z"},
    )
    assert response.status_code == 422


def test_integration_urls_are_bounded_and_hidden_from_readers(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    base = f"/api/v1/organizations/{org.id}/notification-integrations"
    url = "https://hooks.slack.com/services/T0/B0/not-a-real-token"
    row = client.post(base, headers=headers, json={"kind": "slack", "name": "Slack", "url": url}).json()
    viewer = create_user(db_session, email="reader@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    seen = client.get(base, headers=auth_header(client, "reader@example.com"))
    assert seen.json()[0]["url"] == "https://hooks.slack.com/…" and "services" not in seen.text
    assert client.get(base, headers=headers).json()[0]["url"] == url
    long = "https://a.example/" + "x" * 2031
    assert client.post(base, headers=headers, json={"name": "long", "url": long}).status_code == 422
    assert client.patch(f"{base}/{row['id']}", headers=headers, json={"url": long}).status_code == 422
