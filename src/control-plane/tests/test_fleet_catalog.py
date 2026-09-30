"""Tests for device type and device group management."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def test_create_and_list_device_types(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=headers,
        json={"name": "Gateway", "description": "Edge gateway", "capabilities": {"gpio": True}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Gateway"
    assert response.json()["capabilities"] == {"gpio": True}

    listed = client.get(f"/api/v1/organizations/{org.id}/device-types", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_duplicate_device_type_name_rejected(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=headers,
        json={"name": "Gateway"},
    )
    dup = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=headers,
        json={"name": "gateway"},
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "device_type_exists"


def test_member_cannot_create_device_type(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.MEMBER)
    headers = auth_header(client, "member@example.com")

    response = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=headers,
        json={"name": "Gateway"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_permission"


def test_member_can_view_device_types(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    member = create_user(db_session, email="member@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.VIEWER)
    owner_headers = auth_header(client, "owner@example.com")
    client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=owner_headers,
        json={"name": "Gateway"},
    )

    viewer_headers = auth_header(client, "member@example.com")
    response = client.get(f"/api/v1/organizations/{org.id}/device-types", headers=viewer_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_cross_tenant_device_type_access_returns_404(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    stranger = create_user(db_session, email="stranger@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    create_org_with_owner(db_session, stranger, slug="stranger-org")

    owner_headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=owner_headers,
        json={"name": "Gateway"},
    ).json()

    stranger_headers = auth_header(client, "stranger@example.com")
    response = client.get(
        f"/api/v1/organizations/{org.id}/device-types/{created['id']}",
        headers=stranger_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "organization_not_found"


def test_update_and_delete_device_type(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    created = client.post(
        f"/api/v1/organizations/{org.id}/device-types",
        headers=headers,
        json={"name": "Gateway"},
    ).json()

    updated = client.patch(
        f"/api/v1/organizations/{org.id}/device-types/{created['id']}",
        headers=headers,
        json={"description": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated"

    deleted = client.delete(
        f"/api/v1/organizations/{org.id}/device-types/{created['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204


def test_device_group_crud(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    created = client.post(
        f"/api/v1/organizations/{org.id}/device-groups",
        headers=headers,
        json={"name": "Production", "labels": {"tier": "prod"}},
    )
    assert created.status_code == 201
    group_id = created.json()["id"]

    listed = client.get(f"/api/v1/organizations/{org.id}/device-groups", headers=headers)
    assert len(listed.json()) == 1

    deleted = client.delete(f"/api/v1/organizations/{org.id}/device-groups/{group_id}", headers=headers)
    assert deleted.status_code == 204
