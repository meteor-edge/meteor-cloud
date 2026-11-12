"""Audit rows for the actions that were not recorded before, and the audit list filters."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def _events(client: TestClient, org_id, headers, **params) -> list[dict]:
    response = client.get(f"/api/v1/organizations/{org_id}/audit-events", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _setup(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    return owner, org, auth_header(client, "owner@example.com")


def test_rows_carry_client_ip_user_agent_and_actor(client: TestClient, db_session: Session) -> None:
    owner, org, headers = _setup(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers={**headers, "User-Agent": "audit-test/1.0"},
        json={"name": "Gateway"},
    )
    assert response.status_code == 201, response.text

    (event,) = _events(client, org.id, headers, action="device_type.create")
    assert event["actor_type"] == "user"
    assert event["actor_id"] == str(owner.id)
    assert event["actor_user_id"] == str(owner.id)
    assert event["actor_email"] == "owner@example.com"
    assert event["user_agent"] == "audit-test/1.0"
    assert event["ip"]  # TestClient's peer address
    assert event["request_id"]
    assert event["metadata"]["name"] == "Gateway"


def test_failed_login_is_audited_for_the_account_organizations(client: TestClient, db_session: Session) -> None:
    owner, org, headers = _setup(client, db_session)
    member = create_user(db_session, email="member@example.com")
    add_member(db_session, org, member, OrganizationRole.OPERATOR)

    refused = client.post("/api/v1/auth/login", json={"email": "member@example.com", "password": "wrong-password"})
    assert refused.status_code == 401
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert unknown.status_code == 401

    failures = _events(client, org.id, headers, action="auth.login", outcome="failure")
    assert len(failures) == 1
    assert failures[0]["resource_id"] == str(member.id)
    assert failures[0]["actor_type"] == "anonymous"
    assert failures[0]["metadata"] == {"reason": "bad_password"}
    assert "wrong-password" not in str(failures)

    # Unknown emails belong to no organization; the row is still kept.
    global_rows = db_session.scalars(
        select(AuditEvent).where(AuditEvent.organization_id.is_(None), AuditEvent.action == "auth.login")
    ).all()
    assert [row.metadata_["email"] for row in global_rows] == ["nobody@example.com"]


def test_register_is_audited(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "strong-password", "full_name": "New User"},
    )
    assert response.status_code == 201, response.text
    row = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "auth.register")).one()
    assert row.resource_id == response.json()["id"]


def test_forbidden_request_records_access_denied(client: TestClient, db_session: Session) -> None:
    _owner, org, headers = _setup(client, db_session)
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)

    refused = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=auth_header(client, "viewer@example.com"),
        json={"name": "Sneaky"},
    )
    assert refused.status_code == 403

    (event,) = _events(client, org.id, headers, action="access.denied")
    assert event["outcome"] == "denied"
    assert event["actor_user_id"] == str(viewer.id)
    assert event["metadata"]["method"] == "POST"
    assert event["metadata"]["path"] == "/api/v1/organizations/{organization_id}/enrollment-keys"


def test_organization_changes_and_leave_are_audited(client: TestClient, db_session: Session) -> None:
    owner, org, headers = _setup(client, db_session)
    member = create_user(db_session, email="member@example.com")
    add_member(db_session, org, member, OrganizationRole.OPERATOR)

    updated = client.patch(f"/api/v1/organizations/{org.id}", headers=headers, json={"name": "Renamed"})
    assert updated.status_code == 200, updated.text
    left = client.post(f"/api/v1/organizations/{org.id}/leave", headers=auth_header(client, "member@example.com"))
    assert left.status_code == 204, left.text

    (update,) = _events(client, org.id, headers, action="organization.update")
    assert update["metadata"]["changes"]["name"] == {"from": "Acme Energy", "to": "Renamed"}
    (leave,) = _events(client, org.id, headers, action="member.leave")
    assert leave["actor_user_id"] == str(member.id)

    deleted = client.delete(f"/api/v1/organizations/{org.id}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    row = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "organization.delete")).one()
    assert row.organization_id is None  # the organization is gone
    assert row.metadata_["organization_id"] == str(org.id)
    assert row.actor_user_id == owner.id


def test_device_lifecycle_is_audited_with_device_actor(client: TestClient, db_session: Session) -> None:
    _owner, org, headers = _setup(client, db_session)
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "Bootstrap"}
    ).json()
    registered = client.post(
        "/api/v1/agent/register",
        json={"token": token["token"], "mac_addresses": ["aa:bb:cc:dd:ee:01"], "name": "edge-01"},
    )
    assert registered.status_code in (200, 201), registered.text
    device_id = registered.json()["device_id"]

    (register,) = _events(client, org.id, headers, action="device.register")
    assert register["actor_type"] == "registration_token"
    assert register["resource_id"] == device_id
    assert registered.json()["device_token"] not in str(register)

    assert (
        client.patch(
            f"/api/v1/organizations/{org.id}/devices/{device_id}", headers=headers, json={"name": "edge-renamed"}
        ).status_code
        == 200
    )
    device_url = f"/api/v1/organizations/{org.id}/devices/{device_id}"
    assert client.post(f"{device_url}/disable", headers=headers).status_code == 200
    rotated = client.post(f"{device_url}/rotate-credential", headers=headers)
    assert rotated.status_code == 200, rotated.text
    assert (
        client.post(
            f"/api/v1/organizations/{org.id}/devices/{device_id}/revoke-credential", headers=headers
        ).status_code
        == 200
    )

    device_events = _events(client, org.id, headers, resource_type="device", resource_id=device_id)
    actions = [item["action"] for item in device_events]
    assert actions[:4] == ["device.credential.revoke", "device.credential.rotate", "device.disable", "device.update"]
    update = device_events[3]
    assert update["metadata"]["changes"]["name"] == {"from": "edge-01", "to": "edge-renamed"}
    assert rotated.json()["token"] not in str(device_events)


def test_device_group_crud_is_audited(client: TestClient, db_session: Session) -> None:
    _owner, org, headers = _setup(client, db_session)
    group = client.post(f"/api/v1/organizations/{org.id}/device-groups", headers=headers, json={"name": "Roof"})
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    assert (
        client.patch(
            f"/api/v1/organizations/{org.id}/device-groups/{group_id}", headers=headers, json={"name": "Roof 2"}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/organizations/{org.id}/device-groups/{group_id}", headers=headers).status_code == 204

    actions = [item["action"] for item in _events(client, org.id, headers, action="device_group.")]
    assert actions == ["device_group.delete", "device_group.update", "device_group.create"]


def test_list_pages_with_before_cursor(client: TestClient, db_session: Session) -> None:
    _owner, org, headers = _setup(client, db_session)
    for name in ("A", "B", "C"):
        assert (
            client.post(
                f"/api/v1/organizations/{org.id}/device-types", headers=headers, json={"name": name}
            ).status_code
            == 201
        )

    first = _events(client, org.id, headers, action="device_type.*", limit=2)
    assert [item["metadata"]["name"] for item in first] == ["C", "B"]
    rest = _events(
        client,
        org.id,
        headers,
        action="device_type.*",
        limit=2,
        before=first[-1]["created_at"],
        before_id=first[-1]["id"],
    )
    assert [item["metadata"]["name"] for item in rest] == ["A"]
