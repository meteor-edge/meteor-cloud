"""RBAC effective access, scope, and permission enforcement."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.authorization.service import AuthzService
from app.devices.models import Device, DeviceGroup
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def test_me_access_lists_permissions_for_each_org(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    response = client.get("/api/v1/me/access", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["organizations"]) == 1
    entry = payload["organizations"][0]
    assert entry["organization_id"] == str(org.id)
    assert entry["role"]["key"] == "owner"
    assert "device.reboot" in entry["permissions"]
    assert "organization.delete" in entry["permissions"]
    assert entry["scope"]["mode"] == "organization"


def test_operator_can_reboot_but_not_delete_or_manage_members(
    client: TestClient, db_session: Session
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    operator = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    headers = auth_header(client, "ops@example.com")

    group = DeviceGroup(
        organization_id=org.id,
        name="Berlin",
        slug="berlin",
        labels={},
        metadata_={},
    )
    db_session.add(group)
    db_session.flush()
    device = Device(
        organization_id=org.id,
        name="edge-01",
        device_group_id=group.id,
        is_enabled=True,
        labels={},
        metadata_={},
        mac_addresses=[],
    )
    db_session.add(device)
    db_session.commit()

    assert client.get(f"/api/v1/organizations/{org.id}/devices/{device.id}", headers=headers).status_code == 200
    assert (
        client.delete(f"/api/v1/organizations/{org.id}/devices/{device.id}", headers=headers).status_code == 403
    )
    assert client.get(f"/api/v1/organizations/{org.id}/members", headers=headers).status_code == 403


def test_scoped_operator_cannot_access_other_device_group(
    client: TestClient, db_session: Session
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    operator = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)

    berlin = DeviceGroup(organization_id=org.id, name="Berlin", slug="berlin", labels={}, metadata_={})
    munich = DeviceGroup(organization_id=org.id, name="Munich", slug="munich", labels={}, metadata_={})
    db_session.add_all([berlin, munich])
    db_session.flush()

    authz = AuthzService(db_session)
    authz.replace_device_group_scope(membership, [berlin.id])
    db_session.commit()

    device_berlin = Device(
        organization_id=org.id,
        name="berlin-1",
        device_group_id=berlin.id,
        is_enabled=True,
        labels={},
        metadata_={},
        mac_addresses=[],
    )
    device_munich = Device(
        organization_id=org.id,
        name="munich-1",
        device_group_id=munich.id,
        is_enabled=True,
        labels={},
        metadata_={},
        mac_addresses=[],
    )
    ungrouped = Device(
        organization_id=org.id,
        name="loose-1",
        device_group_id=None,
        is_enabled=True,
        labels={},
        metadata_={},
        mac_addresses=[],
    )
    db_session.add_all([device_berlin, device_munich, ungrouped])
    db_session.commit()

    headers = auth_header(client, "ops@example.com")
    assert client.get(f"/api/v1/organizations/{org.id}/devices/{device_berlin.id}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/organizations/{org.id}/devices/{device_munich.id}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/organizations/{org.id}/devices/{ungrouped.id}", headers=headers).status_code == 403


def test_member_access_and_preview(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    operator = create_user(db_session, email="ops@example.com", full_name="Ops User")
    org, _ = create_org_with_owner(db_session, owner)
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    berlin = DeviceGroup(organization_id=org.id, name="Berlin", slug="berlin", labels={}, metadata_={})
    db_session.add(berlin)
    db_session.flush()
    AuthzService(db_session).replace_device_group_scope(membership, [berlin.id])
    db_session.commit()

    headers = auth_header(client, "owner@example.com")
    access = client.get(
        f"/api/v1/organizations/{org.id}/members/{membership.id}/access",
        headers=headers,
    )
    assert access.status_code == 200, access.text
    body = access.json()
    assert body["role"]["key"] == "operator"
    assert body["scope"]["mode"] == "device_groups"
    assert "Berlin" in body["scope"]["device_group_names"]
    assert any(item["id"] == "device.reboot" and item["granted"] for item in body["permissions_detail"])
    assert any(item["id"] == "device.delete" and not item["granted"] for item in body["permissions_detail"])

    device = Device(
        organization_id=org.id,
        name="edge",
        device_group_id=berlin.id,
        is_enabled=True,
        labels={},
        metadata_={},
        mac_addresses=[],
    )
    db_session.add(device)
    db_session.commit()

    preview = client.post(
        f"/api/v1/organizations/{org.id}/access-preview",
        headers=headers,
        json={
            "membership_id": str(membership.id),
            "permission": "device.reboot",
            "resource_type": "device",
            "resource_id": str(device.id),
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["allowed"] is True
