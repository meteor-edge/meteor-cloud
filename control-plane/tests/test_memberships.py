"""Membership API and permission tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationMembership, OrganizationRole
from tests.conftest import auth_header, create_org_with_owner, create_user


def _add_member(
    session: Session,
    organization_id,
    user_id,
    role: OrganizationRole,
) -> OrganizationMembership:
    membership = OrganizationMembership(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
    )
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership


def test_owner_adds_existing_user(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    create_user(db_session, email="member@example.com", full_name="Member User")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "member@example.com", "role": "member"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "member@example.com"
    assert response.json()["role"] == "member"


def test_admin_adds_member(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    create_user(db_session, email="new@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, admin.id, OrganizationRole.ADMIN)

    headers = auth_header(client, "admin@example.com")
    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "new@example.com", "role": "viewer"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "viewer"


def test_duplicate_membership_rejected(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, member.id, OrganizationRole.MEMBER)

    headers = auth_header(client, "owner@example.com")
    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "member@example.com", "role": "member"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "member_already_exists"


def test_admin_cannot_assign_admin(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    create_user(db_session, email="new@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, admin.id, OrganizationRole.ADMIN)

    headers = auth_header(client, "admin@example.com")
    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "new@example.com", "role": "admin"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_permission"


def test_admin_cannot_modify_owner(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    organization, owner_membership = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, admin.id, OrganizationRole.ADMIN)

    headers = auth_header(client, "admin@example.com")
    response = client.patch(
        f"/api/v1/organizations/{organization.id}/members/{owner_membership.id}",
        headers=headers,
        json={"role": "member"},
    )
    assert response.status_code == 403


def test_admin_changes_member_to_viewer(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, admin.id, OrganizationRole.ADMIN)
    membership = _add_member(db_session, organization.id, member.id, OrganizationRole.MEMBER)

    headers = auth_header(client, "admin@example.com")
    response = client.patch(
        f"/api/v1/organizations/{organization.id}/members/{membership.id}",
        headers=headers,
        json={"role": "viewer"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_member_cannot_manage_memberships(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    create_user(db_session, email="new@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, member.id, OrganizationRole.MEMBER)

    headers = auth_header(client, "member@example.com")
    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "new@example.com", "role": "viewer"},
    )
    assert response.status_code == 403


def test_remove_member(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    membership = _add_member(db_session, organization.id, member.id, OrganizationRole.MEMBER)

    headers = auth_header(client, "owner@example.com")
    response = client.delete(
        f"/api/v1/organizations/{organization.id}/members/{membership.id}",
        headers=headers,
    )
    assert response.status_code == 204


def test_last_owner_cannot_be_removed(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, owner_membership = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.delete(
        f"/api/v1/organizations/{organization.id}/members/{owner_membership.id}",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "last_owner_required"


def test_last_owner_cannot_be_demoted(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, owner_membership = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.patch(
        f"/api/v1/organizations/{organization.id}/members/{owner_membership.id}",
        headers=headers,
        json={"role": "admin"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "last_owner_required"


def test_last_owner_cannot_leave(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/leave",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "last_owner_required"


def test_owner_can_leave_when_another_owner_exists(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    co_owner = create_user(db_session, email="coowner@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, co_owner.id, OrganizationRole.OWNER)

    headers = auth_header(client, "owner@example.com")
    response = client.post(
        f"/api/v1/organizations/{organization.id}/leave",
        headers=headers,
    )
    assert response.status_code == 204


def test_owner_promotes_member_to_owner(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    membership = _add_member(db_session, organization.id, member.id, OrganizationRole.MEMBER)

    headers = auth_header(client, "owner@example.com")
    response = client.patch(
        f"/api/v1/organizations/{organization.id}/members/{membership.id}",
        headers=headers,
        json={"role": "owner"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "owner"


def test_cross_organization_membership_access_denied(
    client: TestClient,
    db_session: Session,
) -> None:
    owner_a = create_user(db_session, email="owner-a@example.com")
    owner_b = create_user(db_session, email="owner-b@example.com")
    member = create_user(db_session, email="member@example.com")
    org_a, _ = create_org_with_owner(db_session, owner_a, slug="org-a")
    org_b, _ = create_org_with_owner(db_session, owner_b, slug="org-b")
    membership_b = _add_member(db_session, org_b.id, member.id, OrganizationRole.MEMBER)

    headers = auth_header(client, "owner-a@example.com")
    response = client.delete(
        f"/api/v1/organizations/{org_a.id}/members/{membership_b.id}",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "member_not_found"
