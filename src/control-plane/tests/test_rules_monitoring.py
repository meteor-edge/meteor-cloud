"""Metric catalog, live device view, alert filters, and email and Telegram channels."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.authorization.models import AccessBinding, ScopeType
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.secretbox import unseal
from app.devices.models import DeviceGroup
from app.mqtt.payloads import parse_metrics_payload
from app.rules.dependencies import get_rules_engine_client
from app.rules.models import NotificationIntegration
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_rules import INTERNAL, _definition, _device, _org, _rule, _send, _transition

TOKEN = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQr"


def _report(db: Session, device, *, age: timedelta = timedelta(), **metrics) -> None:
    device.mqtt_metrics = metrics
    device.mqtt_metrics_at = device.last_seen_at = datetime.now(UTC) - age
    db.commit()


def _url(org, path: str) -> str:
    return f"/api/v1/organizations/{org.id}/{path}"


# ---- metrics -------------------------------------------------------------


def _parse(raw: str) -> dict[str, Any]:
    return {k: v for k, v in (parse_metrics_payload(raw) or {}).items() if v is not None}


def test_every_numeric_reading_is_kept_and_bounded() -> None:
    payload = {
        "cpu_percent": 10,
        "load_1m": 0.5,
        "app.web.cpu": 3,
        "temperature_c": 900,
        "name": "x",
        "up": True,
        "bad key": 1,
        "n": None,
        "nested": {"a": 1},
    }
    assert _parse(json.dumps(payload)) == {"cpu_percent": 10.0, "load_1m": 0.5, "app.web.cpu": 3.0}
    huge = {"cpu_percent": 1, "inf": 1e400, **{f"m{i}": i for i in range(500)}}
    kept = _parse(json.dumps(huge))
    assert len(kept) == 201 and "inf" not in kept
    assert _parse('{"cpu_percent":1,"big":1' + "0" * 400 + "}") == {"cpu_percent": 1.0}


def test_metric_catalog_lists_known_and_reported_metrics(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    _report(db_session, _device(db_session, org, "a"), cpu_percent=10.0, **{"app.web.cpu": 4.0}, timestamp="x")
    _report(db_session, _device(db_session, org, "b"), cpu_percent=90.0)
    rows = {m["field"]: m for m in client.get(_url(org, "rules/metrics"), headers=headers).json()}
    cpu = rows["cpu_percent"]
    assert (cpu["label"], cpu["unit"], cpu["devices"], cpu["min"], cpu["max"]) == ("CPU usage", "%", 2, 10.0, 90.0)
    assert rows["wifi_signal_dbm"]["devices"] == 0 and rows["app.web.cpu"]["group"] == "Applications"
    assert "timestamp" not in rows

    found = client.get(_url(org, "rules/metrics"), headers=headers, params={"search": "web CPU"}).json()
    assert [m["field"] for m in found] == ["app.web.cpu"]

    other, other_headers = _org(client, db_session, "other@example.com")
    assert client.get(_url(org, "rules/metrics"), headers=other_headers).status_code == 404
    names = [m["field"] for m in client.get(_url(other, "rules/metrics"), headers=other_headers).json()]
    assert "app.web.cpu" not in names


# ---- live devices ---------------------------------------------------------


def _live(client: TestClient, org, headers, **query) -> dict[str, Any]:
    response = client.post(_url(org, "rules/live-devices"), headers=headers, json=query)
    assert response.status_code == 200, response.text
    return response.json()


def _names(result: dict[str, Any]) -> list[str]:
    return [d["name"] for d in result["devices"]]


def test_live_devices_filter_by_readings_state_and_alerts(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    hot = _device(db_session, org, "hot")
    _report(db_session, hot, cpu_percent=95.0, memory_percent=40.0)
    _report(db_session, _device(db_session, org, "full"), cpu_percent=10.0, memory_percent=91.0)
    _report(db_session, _device(db_session, org, "stale"), age=timedelta(hours=2), cpu_percent=99.0)
    _device(db_session, org, "silent")
    cpu = {"field": "cpu_percent", "operator": "gt", "value": 80}
    mem = {"field": "memory_percent", "operator": "gt", "value": 80}

    everything = _live(client, org, headers)
    assert everything["total"] == 4 and len(everything["devices"]) == 4
    # An old reading is not "now".
    assert _names(_live(client, org, headers, conditions=[cpu])) == ["hot"]
    assert _names(_live(client, org, headers, conditions=[cpu], fresh_minutes=600)) == ["hot", "stale"]
    assert _names(_live(client, org, headers, conditions=[cpu, mem], match="any")) == ["full", "hot"]
    assert _names(_live(client, org, headers, conditions=[cpu, mem])) == []
    between = {"field": "cpu_percent", "operator": "between", "value": 5, "max": 20}
    assert _names(_live(client, org, headers, conditions=[between])) == ["full"]
    assert _names(_live(client, org, headers, state="offline")) == ["silent", "stale"]
    assert _names(_live(client, org, headers, search="HO")) == ["hot"]

    rule = _rule(client, org, headers)
    _send(client, _transition(rule, hot, "firing", 1))
    active = _live(client, org, headers, alerts="active")
    assert _names(active) == ["hot"] and active["devices"][0]["alerts"][0]["rule_name"] == "High CPU"
    assert _names(_live(client, org, headers)) == ["hot", "full", "silent", "stale"]  # alerts first
    assert "hot" not in _names(_live(client, org, headers, alerts="none"))
    page = _live(client, org, headers, limit=1, offset=1)
    assert page["matching"] == 4 and _names(page) == ["full"]

    for bad in ({"field": "a", "operator": "between", "value": 5, "max": 1}, {"field": "a", "operator": "like"}):
        assert (
            client.post(_url(org, "rules/live-devices"), headers=headers, json={"conditions": [bad]}).status_code == 422
        )


def test_scoped_members_only_see_their_groups(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    group = DeviceGroup(organization_id=org.id, name="Store", slug="store")
    db_session.add(group)
    db_session.commit()
    mine = _device(db_session, org, "mine", device_group_id=group.id)
    theirs = _device(db_session, org, "theirs")
    _report(db_session, mine, cpu_percent=95.0)
    _report(db_session, theirs, cpu_percent=95.0, secret_metric=1.0)
    rule = _rule(client, org, headers)
    _send(client, _transition(rule, mine, "firing", 1), _transition(rule, theirs, "firing", 1))
    user = create_user(db_session, email="scoped@example.com")
    membership = add_member(db_session, org, user, OrganizationRole.VIEWER)
    db_session.add(
        AccessBinding(
            membership_id=membership.id,
            scope_type=ScopeType.DEVICE_GROUP.value,
            scope_id=group.id,
        )
    )
    db_session.commit()
    db_session.expire_all()
    scoped = auth_header(client, "scoped@example.com")
    assert _names(_live(client, org, scoped)) == ["mine"]
    fields = [m["field"] for m in client.get(_url(org, "rules/metrics"), headers=scoped).json()]
    assert "secret_metric" not in fields
    alerts = client.get(_url(org, "alerts"), headers=scoped).json()
    assert [a["device_name"] for a in alerts] == ["mine"]
    assert client.get(_url(org, "alerts/summary"), headers=scoped).json()["firing"] == 1
    other = next(a for a in client.get(_url(org, "alerts"), headers=headers).json() if a["device_name"] == "theirs")
    assert client.get(_url(org, f"alerts/{other['id']}"), headers=scoped).status_code == 404


# ---- alerts --------------------------------------------------------------


def test_alert_search_metric_filter_and_summary(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    a, b = _device(db_session, org, "pump_1"), _device(db_session, org, "pumpX1")
    cpu = _rule(client, org, headers)
    mem = _rule(client, org, headers, _definition(name="Memory", severity="warning"))
    _send(
        client,
        _transition(cpu, a, "firing", 1),
        {**_transition(mem, b, "firing", 1, values={"avg(memory_percent)": 91.0}), "severity": "warning"},
    )

    def names(**params):
        return [r["device_name"] for r in client.get(_url(org, "alerts"), headers=headers, params=params).json()]

    assert names(search="pump_1") == ["pump_1"]  # "_" is not a wildcard
    assert names(search="memo") == ["pumpX1"]
    assert names(metric="cpu_percent") == ["pump_1"] and names(metric="memory_percent") == ["pumpX1"]
    assert names(metric="percent") == []
    summary = client.get(_url(org, "alerts/summary"), headers=headers).json()
    assert summary == {
        "firing": 2,
        "acknowledged": 0,
        "devices": 2,
        "by_severity": {"critical": 1, "warning": 1},
        "by_rule": {cpu["id"]: 1, mem["id"]: 1},
    }


def test_a_resolve_closes_every_per_event_alert_of_the_episode(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org)
    rule = _rule(client, org, headers)
    extra = _transition(rule, device, "firing", 1, fingerprint="ev-2")
    extra["idempotency_key"] += ":ev-2"
    _send(client, _transition(rule, device, "firing", 1), extra)
    _send(client, _transition(rule, device, "resolved", 1))
    rows = client.get(_url(org, "alerts"), headers=headers).json()
    assert len(rows) == 2 and {r["status"] for r in rows} == {"resolved"}


# ---- channels ------------------------------------------------------------


class FakeEngine:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {"delivered": True}

    def test_delivery(self, body: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(body)
        return self.result

    def engine(self) -> dict[str, Any]:
        return {"ready": True, "channels": {"email": True}}


class DownEngine:
    def engine(self) -> dict[str, Any]:
        raise AppError("rules_engine_unreachable", "down", status_code=503)


@pytest.fixture
def fake_engine(client: TestClient):
    fake = FakeEngine()
    client.app.dependency_overrides[get_rules_engine_client] = lambda: fake
    yield fake
    client.app.dependency_overrides.pop(get_rules_engine_client, None)


def _channel(client: TestClient, org, headers, expect: int = 201, **body) -> dict[str, Any]:
    response = client.post(_url(org, "notification-integrations"), headers=headers, json=body)
    assert response.status_code == expect, response.text
    return response.json()


def test_email_and_telegram_channels(client: TestClient, db_session: Session, fake_engine: FakeEngine) -> None:
    org, headers = _org(client, db_session)
    device = _device(db_session, org, "pump-7")
    mail = _channel(client, org, headers, kind="email", name="Ops", recipients=["ops@example.com", "ops@example.com"])
    assert mail["recipients"] == ["ops@example.com"] and mail["url"] == "" and not mail["has_secret"]
    chat = _channel(client, org, headers, kind="telegram", name="Bot", chat_id="-1001", secret=TOKEN)
    assert chat["chat_id"] == "-1001" and chat["has_secret"] and TOKEN not in json.dumps(chat)
    row = db_session.get(NotificationIntegration, uuid.UUID(chat["id"]))
    assert unseal(get_settings(), row.secret_sealed, context=f"integration:{row.id}") == TOKEN

    rule = _rule(client, org, headers, _definition(actions=[{"type": "notification", "integration_id": chat["id"]}]))
    body = {
        "integration_id": chat["id"],
        "organization_id": str(org.id),
        "rule_id": rule["id"],
        "device_id": str(device.id),
        "at": datetime.now(UTC).isoformat(),
    }
    answer = client.post("/internal/rules/deliveries", headers=INTERNAL, json=body).json()
    assert answer["kind"] == "telegram" and answer["config"] == {"chat_id": "-1001"} and answer["secret"] == TOKEN
    assert answer["device_name"] == "pump-7" and answer["organization_name"] == org.name

    # Changing the chat keeps the token; it can never be removed from a Telegram channel.
    path = _url(org, f"notification-integrations/{chat['id']}")
    assert client.patch(path, headers=headers, json={"chat_id": "@ops_room"}).json()["chat_id"] == "@ops_room"
    assert client.post("/internal/rules/deliveries", headers=INTERNAL, json=body).json()["secret"] == TOKEN
    assert client.patch(path, headers=headers, json={"secret": ""}).status_code == 422
    assert client.patch(path, headers=headers, json={"url": "https://example.com"}).status_code == 422
    assert client.patch(path, headers=headers, json={"enabled": False}).json()["enabled"] is False

    tested = client.post(f"{path}/test", headers=headers)
    assert tested.json() == {"delivered": True, "error": None}
    # The engine is told which integration, and asks for the rest with the internal token.
    request = {"organization_id": str(org.id), "integration_id": path.rsplit("/", 1)[1]}
    assert fake_engine.sent[0] == request
    got = client.post("/internal/rules/test-deliveries", headers=INTERNAL, json=request).json()
    assert got["secret"] == TOKEN and got["enabled"] is True and got["organization_id"] == str(org.id)
    assert client.post("/internal/rules/test-deliveries", json=request).status_code == 401
    other = request | {"organization_id": str(uuid.uuid4())}
    assert client.post("/internal/rules/test-deliveries", headers=INTERNAL, json=other).status_code == 404
    fake_engine.result = {"delivered": False, "error": "chat not found"}
    assert client.post(f"{path}/test", headers=headers).json() == {"delivered": False, "error": "chat not found"}
    outcomes = db_session.scalars(
        select(AuditEvent.outcome).where(AuditEvent.action == "integration.test").order_by(AuditEvent.created_at)
    ).all()
    assert sorted(outcomes) == ["failure", "success"]
    assert all(TOKEN not in str(e.metadata_) for e in db_session.scalars(select(AuditEvent)))


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "email"},
        {"kind": "email", "recipients": []},
        {"kind": "email", "recipients": ["not-an-address"]},
        {"kind": "email", "recipients": ["a@example.com\r\nBcc: b@example.com"]},
        {"kind": "email", "recipients": [f"u{i}@example.com" for i in range(21)]},
        {"kind": "email", "recipients": ["a@example.com"], "url": "https://example.com"},
        {"kind": "telegram", "chat_id": "1"},
        {"kind": "telegram", "secret": TOKEN},
        {"kind": "telegram", "chat_id": "1", "secret": "123/../../x"},
        {"kind": "telegram", "chat_id": "1\n", "secret": TOKEN},
        {"kind": "telegram", "chat_id": "not a chat", "secret": TOKEN},
        {"kind": "webhook"},
        {"kind": "webhook", "url": "https://example.com", "recipients": ["a@example.com"]},
        {"kind": "webhook", "url": "https://example.com", "secret": "short"},
        {"kind": "sms"},
    ],
)
def test_channel_validation(client: TestClient, db_session: Session, body: dict[str, Any]) -> None:
    org, headers = _org(client, db_session)
    _channel(client, org, headers, expect=422, name="x", **body)


def test_channel_permissions_and_capabilities(client: TestClient, db_session: Session, fake_engine: FakeEngine) -> None:
    org, headers = _org(client, db_session)
    channel = _channel(client, org, headers, kind="email", name="Ops", recipients=["ops@example.com"])
    test_path = f"notification-integrations/{channel['id']}/test"
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    viewer_headers = auth_header(client, "viewer@example.com")
    assert client.post(_url(org, test_path), headers=viewer_headers).status_code == 403
    other, other_headers = _org(client, db_session, "other@example.com")
    assert client.post(_url(other, test_path), headers=other_headers).status_code == 404
    assert fake_engine.sent == []
    path = _url(org, "notification-integrations/capabilities")
    assert client.get(path, headers=viewer_headers).json() == {"engine": True, "email": True}
    client.app.dependency_overrides[get_rules_engine_client] = DownEngine
    assert client.get(path, headers=headers).json() == {"engine": False, "email": False}
