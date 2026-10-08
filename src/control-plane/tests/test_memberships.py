"""Membership API and permission tests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.local_identity import LocalIdentityDirectory
from app.authorization.roles import get_system_role_id
from app.core.database import get_db
from app.tenancy.models import OrganizationMembership, OrganizationRole
from app.tenancy.router import get_identity_directory
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
        role_id=get_system_role_id(session, role.value),
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
        json={"email": "member@example.com", "role": "operator"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "member@example.com"
    assert response.json()["role"] == "operator"


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
    _add_member(db_session, organization.id, member.id, OrganizationRole.OPERATOR)

    headers = auth_header(client, "owner@example.com")
    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "member@example.com", "role": "operator"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "member_already_exists"


def test_add_member_creates_new_user(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={
            "email": "Fresh@Example.com",
            "full_name": "  Fresh User ",
            "password": "temporary-pass",
            "role": "viewer",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "fresh@example.com"
    assert body["full_name"] == "Fresh User"
    assert body["role"] == "viewer"

    new_headers = auth_header(client, "fresh@example.com", password="temporary-pass")
    listed = client.get("/api/v1/organizations", headers=new_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(organization.id)]


def test_add_member_new_email_requires_account_details(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={"email": "nobody@example.com", "role": "viewer"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "member_account_details_required"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "temporary-pass"},
    )
    assert login.status_code == 401


def test_add_member_rejects_weak_password(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={
            "email": "weak@example.com",
            "full_name": "Weak",
            "password": "short",
            "role": "viewer",
        },
    )
    assert response.status_code == 422


def test_add_existing_user_ignores_account_details(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    create_user(db_session, email="member@example.com", full_name="Member User")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={
            "email": "member@example.com",
            "full_name": "Someone Else",
            "password": "another-password",
            "role": "operator",
        },
    )
    assert response.status_code == 201
    assert response.json()["full_name"] == "Member User"
    auth_header(client, "member@example.com")


def test_add_member_uses_injected_identity_directory(
    client: TestClient,
    db_session: Session,
) -> None:
    calls: list[str] = []

    class RecordingDirectory(LocalIdentityDirectory):
        def ensure_account(self, *, email: str, full_name: str, password: str):
            calls.append(email)
            return super().ensure_account(email=email, full_name=full_name, password=password)

    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    def override(session: Annotated[Session, Depends(get_db)]) -> RecordingDirectory:
        return RecordingDirectory(session)

    client.app.dependency_overrides[get_identity_directory] = override
    try:
        response = client.post(
            f"/api/v1/organizations/{organization.id}/members",
            headers=headers,
            json={
                "email": "injected@example.com",
                "full_name": "Injected",
                "password": "temporary-pass",
                "role": "viewer",
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_identity_directory, None)
    assert response.status_code == 201, response.text
    assert calls == ["injected@example.com"]


def test_add_inactive_user_rejected(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    create_user(db_session, email="disabled@example.com", is_active=False)
    organization, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{organization.id}/members",
        headers=headers,
        json={
            "email": "disabled@example.com",
            "full_name": "Disabled",
            "password": "temporary-pass",
            "role": "viewer",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "member_not_found"


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
        json={"role": "operator"},
    )
    assert response.status_code == 403


def test_admin_changes_member_to_viewer(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner)
    _add_member(db_session, organization.id, admin.id, OrganizationRole.ADMIN)
    membership = _add_member(db_session, organization.id, member.id, OrganizationRole.OPERATOR)

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
    _add_member(db_session, organization.id, member.id, OrganizationRole.OPERATOR)

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
    membership = _add_member(db_session, organization.id, member.id, OrganizationRole.OPERATOR)

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
    membership = _add_member(db_session, organization.id, member.id, OrganizationRole.OPERATOR)

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
    membership_b = _add_member(db_session, org_b.id, member.id, OrganizationRole.OPERATOR)

    headers = auth_header(client, "owner-a@example.com")
    response = client.delete(
        f"/api/v1/organizations/{org_a.id}/members/{membership_b.id}",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "member_not_found"
