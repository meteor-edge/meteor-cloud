"""Who may do what with rules, and what never leaves the control plane."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.authorization.models import AccessBinding, ScopeType
from app.device_actions.models import DeviceAction
from app.devices.models import DeviceGroup
from app.rules.dependencies import get_rules_engine_client
from app.rules.models import NotificationIntegration
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_rules import INTERNAL, _definition, _device, _org, _rule, _send, _transition
from tests.test_rules_dispatch import _action


def _scoped(client: TestClient, db_session: Session, org, group, email: str = "scoped@example.com") -> dict:
    user = create_user(db_session, email=email)
    membership = add_member(db_session, org, user, OrganizationRole.ADMIN)
    db_session.add(
        AccessBinding(membership_id=membership.id, scope_type=ScopeType.DEVICE_GROUP.value, scope_id=group.id)
    )
    db_session.commit()
    db_session.expire_all()
    return auth_header(client, email)


def _world(client: TestClient, db_session: Session):
    org, headers = _org(client, db_session)
    group = DeviceGroup(organization_id=org.id, name="Store", slug="store")
    db_session.add(group)
    db_session.commit()
    mine = _device(db_session, org, "mine", device_group_id=group.id)
    theirs = _device(db_session, org, "theirs")
    return org, headers, group, mine, theirs, _scoped(client, db_session, org, group)


def test_scoped_members_manage_only_rules_of_their_groups(client: TestClient, db_session: Session) -> None:
    org, headers, group, mine, theirs, scoped = _world(client, db_session)
    base = f"/api/v1/organizations/{org.id}/rules"
    inside = [{"type": "group", "group_id": str(group.id)}, {"type": "device", "device_ids": [str(mine.id)]}]
    outside = [{"type": "organization"}, {"type": "device", "device_ids": [str(mine.id), str(theirs.id)]}]
    for scope in inside:
        rule = _rule(client, org, scoped, _definition(scope=scope))
        for wider in outside:
            body = {"definition": _definition(scope=wider)}
            assert client.put(f"{base}/{rule['id']}", headers=scoped, json=body).status_code == 403
    for scope in outside:
        body = {"definition": _definition(scope=scope)}
        assert client.post(base, headers=scoped, json=body).status_code == 403
    wide = _rule(client, org, headers)
    body = {"definition": _definition(scope=inside[0])}
    assert client.put(f"{base}/{wide['id']}", headers=scoped, json=body).status_code == 403
    assert client.post(f"{base}/{wide['id']}/disable", headers=scoped).status_code == 403
    assert client.delete(f"{base}/{wide['id']}", headers=scoped).status_code == 403
    assert client.put(f"{base}/{wide['id']}", headers=headers, json=body).status_code == 200


def test_scoped_members_preview_acknowledge_and_silence_only_their_devices(
    client: TestClient, db_session: Session
) -> None:
    org, headers, _, mine, theirs, scoped = _world(client, db_session)
    base = f"/api/v1/organizations/{org.id}"
    body = {"definition": {"scope": {"type": "organization"}}}
    preview = client.post(f"{base}/rules/preview-devices", headers=scoped, json=body).json()
    assert [d["name"] for d in preview["devices"]] == ["mine"] and preview["total"] == 1
    rule = _rule(client, org, headers)
    _send(client, _transition(rule, mine, "firing", 1), _transition(rule, theirs, "firing", 1))
    ids = {a["device_name"]: a["id"] for a in client.get(f"{base}/alerts", headers=headers).json()}
    assert client.post(f"{base}/alerts/{ids['theirs']}/acknowledge", headers=scoped).status_code == 404
    assert client.post(f"{base}/alerts/{ids['mine']}/acknowledge", headers=scoped).status_code == 200
    end = {"ends_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    silences = f"{base}/alert-silences"
    for target in ({}, {"rule_id": rule["id"]}, {"device_id": str(theirs.id)}):
        assert client.post(silences, headers=scoped, json=end | target).status_code == 403
    assert client.post(silences, headers=scoped, json=end | {"device_id": str(mine.id)}).status_code == 201
    everything = client.post(silences, headers=headers, json=end).json()
    assert client.delete(f"{silences}/{everything['id']}", headers=scoped).status_code == 403


def test_status_pages_show_only_visible_devices(client: TestClient, db_session: Session) -> None:
    org, headers, _, mine, theirs, scoped = _world(client, db_session)
    devices = sorted([str(mine.id), str(theirs.id)])

    class Engine:
        def status(self, rule_id: str, *, after: str = "", limit: int = 100) -> dict[str, Any]:
            return {"devices": [{"device_id": d} for d in devices if d > after][:limit]}

    rule = _rule(client, org, headers)
    client.app.dependency_overrides[get_rules_engine_client] = Engine
    try:
        path = f"/api/v1/organizations/{org.id}/rules/{rule['id']}/status?limit=1"
        seen = client.get(path, headers=scoped).json()["devices"]
        assert [d["device_id"] for d in seen] == [str(mine.id)]
        assert len(client.get(path, headers=headers).json()["devices"]) == 1
    finally:
        client.app.dependency_overrides.pop(get_rules_engine_client)


def test_commands_run_as_the_author_of_the_version(client: TestClient, db_session: Session) -> None:
    """An edit by someone limited to a device group must not use the first author's wider rights."""
    org, headers, group, mine, theirs, scoped = _world(client, db_session)
    actions = [{"type": "command", "command": "reboot"}]
    rule = _rule(client, org, headers, _definition(scope={"type": "group", "group_id": str(group.id)}, actions=actions))
    body = {"definition": _definition(scope={"type": "group", "group_id": str(group.id)}, actions=actions)}
    path = f"/api/v1/organizations/{org.id}/rules/{rule['id']}"
    edited = client.put(path, headers=scoped, json=body).json()
    assert edited["current_version"] == 2
    command = _action(edited, theirs, type="command", command="reboot")
    refused = client.post("/internal/rules/commands", headers=INTERNAL, json=command)
    assert refused.status_code in (403, 404), refused.text
    assert db_session.scalar(select(DeviceAction).where(DeviceAction.device_id == theirs.id)) is None
    sent = client.post(
        "/internal/rules/commands", headers=INTERNAL, json=_action(edited, mine, type="command", command="reboot")
    )
    assert sent.json()["status"] == "sent", sent.text


def test_integration_urls_are_sealed(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    base = f"/api/v1/organizations/{org.id}/notification-integrations"
    url = "https://user:pw@hooks.slack.com/services/T0/B0/not-a-real-token"
    row = client.post(base, headers=headers, json={"kind": "slack", "name": "Slack", "url": url}).json()
    assert row["url"] == url
    stored = db_session.scalar(select(NotificationIntegration))
    assert stored.url == "https://hooks.slack.com/…" and b"not-a-real" not in stored.url_sealed
    # Other changes keep it; a new URL replaces it.
    assert client.patch(f"{base}/{row['id']}", headers=headers, json={"name": "S"}).json()["url"] == url
    moved = client.patch(f"{base}/{row['id']}", headers=headers, json={"url": "https://b.example/y"}).json()
    assert moved["url"] == "https://b.example/y"
    assert all("not-a-real" not in str(e.metadata_) for e in db_session.scalars(select(AuditEvent)))
