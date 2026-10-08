"""RBAC effective access, scope, and permission enforcement."""

from __future__ import annotations

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


def test_operator_can_read_device_but_not_delete_or_manage_members(
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

    device_url = f"/api/v1/organizations/{org.id}/devices/{device_berlin.id}"
    assert client.patch(device_url, headers=headers, json={"device_group_id": str(munich.id)}).status_code == 403
    assert client.patch(device_url, headers=headers, json={"clear_device_group": True}).status_code == 403
    db_session.refresh(device_berlin)
    assert device_berlin.device_group_id == berlin.id


def test_disabled_member_cannot_use_fleet_or_artifacts(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    operator = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    membership.status = "disabled"
    db_session.commit()

    owner_headers = auth_header(client, "owner@example.com")
    headers = auth_header(client, "ops@example.com")
    for path in ("devices", "artifacts"):
        url = f"/api/v1/organizations/{org.id}/{path}"
        assert client.get(url, headers=owner_headers).status_code == 200
        assert client.get(url, headers=headers).status_code == 404


def test_scoped_list_filters_before_pagination(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    operator = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)

    berlin = DeviceGroup(organization_id=org.id, name="Berlin", slug="berlin", labels={}, metadata_={})
    munich = DeviceGroup(organization_id=org.id, name="Munich", slug="munich", labels={}, metadata_={})
    paris = DeviceGroup(organization_id=org.id, name="Paris", slug="paris", labels={}, metadata_={})
    db_session.add_all([berlin, munich, paris])
    db_session.flush()
    AuthzService(db_session).replace_device_group_scope(membership, [berlin.id, munich.id])
    assert {b.scope_id for b in membership.bindings} == {berlin.id, munich.id}

    # Out-of-scope devices sort first so a post-pagination filter would return an empty first page.
    for name, group_id in [("a-paris", paris.id), ("b-loose", None), ("c-berlin", berlin.id), ("d-munich", munich.id)]:
        db_session.add(
            Device(
                organization_id=org.id,
                name=name,
                device_group_id=group_id,
                is_enabled=True,
                labels={},
                metadata_={},
                mac_addresses=[],
            )
        )
    db_session.commit()

    headers = auth_header(client, "ops@example.com")
    response = client.get(f"/api/v1/organizations/{org.id}/devices?page_size=1", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["c-berlin"]

    groups = client.get(f"/api/v1/organizations/{org.id}/device-groups", headers=headers)
    assert groups.status_code == 200, groups.text
    assert sorted(item["name"] for item in groups.json()) == ["Berlin", "Munich"]


def test_preview_denies_inactive_membership(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    operator = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    membership.status = "disabled"
    db_session.commit()

    preview = client.post(
        f"/api/v1/organizations/{org.id}/access-preview",
        headers=auth_header(client, "owner@example.com"),
        json={"membership_id": str(membership.id), "permission": "device.read"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["allowed"] is False
    assert body["steps"][0]["ok"] is False


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
