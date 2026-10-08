"""Organization API tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.authorization.roles import get_system_role_id
from app.tenancy.models import OrganizationMembership, OrganizationRole
from tests.conftest import auth_header, create_org_with_owner, create_user


def test_create_organization_makes_creator_owner(
    client: TestClient,
    db_session: Session,
) -> None:
    create_user(db_session, email="owner@example.com")
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={
            "name": "Acme Energy",
            "slug": "acme-energy",
            "description": "Edge-device management organization",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Acme Energy"
    assert payload["slug"] == "acme-energy"
    assert payload["current_user_role"] == "owner"
    assert payload["member_count"] == 1


def test_list_only_user_organizations(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    other = create_user(db_session, email="other@example.com")
    create_org_with_owner(db_session, owner, slug="owner-org")
    create_org_with_owner(db_session, other, slug="other-org")

    headers = auth_header(client, "owner@example.com")
    response = client.get("/api/v1/organizations", headers=headers)
    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()}
    assert slugs == {"owner-org"}


def test_get_organization_as_member(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="acme")
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=member.id,
            role_id=get_system_role_id(db_session, OrganizationRole.OPERATOR.value),
        )
    )
    db_session.commit()

    headers = auth_header(client, "member@example.com")
    response = client.get(f"/api/v1/organizations/{organization.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["current_user_role"] == "operator"


def test_unauthorized_organization_access_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    create_user(db_session, email="stranger@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="secret")

    headers = auth_header(client, "stranger@example.com")
    response = client.get(f"/api/v1/organizations/{organization.id}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "organization_not_found"


def test_update_organization_as_owner(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="acme")
    headers = auth_header(client, "owner@example.com")

    response = client.patch(
        f"/api/v1/organizations/{organization.id}",
        headers=headers,
        json={"name": "Acme Updated", "description": "Updated"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Acme Updated"


def test_update_organization_as_admin(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="acme")
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=admin.id,
            role_id=get_system_role_id(db_session, OrganizationRole.ADMIN.value),
        )
    )
    db_session.commit()

    headers = auth_header(client, "admin@example.com")
    response = client.patch(
        f"/api/v1/organizations/{organization.id}",
        headers=headers,
        json={"description": "Admin update"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Admin update"


def test_update_organization_denied_for_member(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="acme")
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=member.id,
            role_id=get_system_role_id(db_session, OrganizationRole.OPERATOR.value),
        )
    )
    db_session.commit()

    headers = auth_header(client, "member@example.com")
    response = client.patch(
        f"/api/v1/organizations/{organization.id}",
        headers=headers,
        json={"name": "Nope"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_permission"


def test_delete_organization_as_owner(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="acme")
    headers = auth_header(client, "owner@example.com")

    response = client.delete(f"/api/v1/organizations/{organization.id}", headers=headers)
    assert response.status_code == 204


def test_delete_denied_for_admin(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    admin = create_user(db_session, email="admin@example.com")
    organization, _ = create_org_with_owner(db_session, owner, slug="acme")
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=admin.id,
            role_id=get_system_role_id(db_session, OrganizationRole.ADMIN.value),
        )
    )
    db_session.commit()

    headers = auth_header(client, "admin@example.com")
    response = client.delete(f"/api/v1/organizations/{organization.id}", headers=headers)
    assert response.status_code == 403


def test_duplicate_slug_rejection(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    create_org_with_owner(db_session, owner, slug="acme-energy")
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "Another", "slug": "acme-energy"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "organization_slug_exists"


def test_unknown_organization_id_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    create_user(db_session, email="owner@example.com")
    headers = auth_header(client, "owner@example.com")
    response = client.get(f"/api/v1/organizations/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
