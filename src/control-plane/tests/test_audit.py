"""Audit event API tests."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def test_enrollment_key_create_is_audited(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    created = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Field techs"},
    )
    assert created.status_code == 201, created.text

    events = client.get(f"/api/v1/organizations/{org.id}/audit-events", headers=headers)
    assert events.status_code == 200, events.text
    body = events.json()
    assert any(item["action"] == "enrollment_key.create" for item in body)
    assert "api_key" not in str(body)


def test_login_is_listed_in_organization_audit(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    member = create_user(db_session, email="member@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, admin, OrganizationRole.ADMIN)
    add_member(db_session, org, member, OrganizationRole.OPERATOR)

    auth_header(client, "member@example.com")
    events = client.get(
        f"/api/v1/organizations/{org.id}/audit-events",
        headers=auth_header(client, "admin@example.com"),
    )
    assert events.status_code == 200, events.text
    assert any(item["action"] == "auth.login" and item["actor_user_id"] == str(member.id) for item in events.json())


def test_member_cannot_list_audit_events(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.OPERATOR)

    response = client.get(
        f"/api/v1/organizations/{org.id}/audit-events",
        headers=auth_header(client, "member@example.com"),
    )
    assert response.status_code == 403
