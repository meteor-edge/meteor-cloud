"""Team API tests: teams group members and grant no permissions."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def _teams_url(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/teams"


def test_owner_creates_team_with_members(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com", full_name="Olivia Owner")
    ops = create_user(db_session, email="ops@example.com", full_name="Oscar Ops")
    org, owner_membership = create_org_with_owner(db_session, owner)
    ops_membership = add_member(db_session, org, ops, OrganizationRole.OPERATOR)
    headers = auth_header(client, "owner@example.com")

    response = client.post(
        _teams_url(org.id),
        headers=headers,
        json={
            "name": "  Berlin on-call ",
            "description": "Night shift",
            "membership_ids": [str(ops_membership.id), str(owner_membership.id)],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Berlin on-call"
    assert body["member_count"] == 2
    assert [item["full_name"] for item in body["members"]] == ["Olivia Owner", "Oscar Ops"]

    listed = client.get(_teams_url(org.id), headers=headers)
    assert listed.status_code == 200
    assert [(item["name"], item["member_count"]) for item in listed.json()] == [("Berlin on-call", 2)]

    members = client.get(f"/api/v1/organizations/{org.id}/members", headers=headers).json()
    by_email = {item["email"]: item for item in members}
    assert [team["name"] for team in by_email["ops@example.com"]["teams"]] == ["Berlin on-call"]


def test_team_names_are_unique_per_org_case_insensitive(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")

    assert client.post(_teams_url(org.id), headers=headers, json={"name": "Field"}).status_code == 201
    duplicate = client.post(_teams_url(org.id), headers=headers, json={"name": "field"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "team_exists"


def test_add_and_remove_team_member(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    dev = create_user(db_session, email="dev@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    dev_membership = add_member(db_session, org, dev, OrganizationRole.DEVELOPER)
    headers = auth_header(client, "owner@example.com")
    team_id = client.post(_teams_url(org.id), headers=headers, json={"name": "Firmware"}).json()["id"]

    added = client.post(
        f"{_teams_url(org.id)}/{team_id}/members",
        headers=headers,
        json={"membership_id": str(dev_membership.id)},
    )
    assert added.status_code == 200
    assert added.json()["member_count"] == 1

    again = client.post(
        f"{_teams_url(org.id)}/{team_id}/members",
        headers=headers,
        json={"membership_id": str(dev_membership.id)},
    )
    assert again.status_code == 409

    removed = client.delete(f"{_teams_url(org.id)}/{team_id}/members/{dev_membership.id}", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["members"] == []


def test_member_from_other_org_cannot_join_team(client: TestClient, db_session: Session) -> None:
    owner_a = create_user(db_session, email="owner-a@example.com")
    owner_b = create_user(db_session, email="owner-b@example.com")
    org_a, _ = create_org_with_owner(db_session, owner_a, slug="org-a")
    _, owner_b_membership = create_org_with_owner(db_session, owner_b, slug="org-b")
    headers = auth_header(client, "owner-a@example.com")

    response = client.post(
        _teams_url(org_a.id),
        headers=headers,
        json={"name": "Mixed", "membership_ids": [str(owner_b_membership.id)]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "member_not_found"


def test_removing_org_member_drops_them_from_teams(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    ops = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    ops_membership = add_member(db_session, org, ops, OrganizationRole.OPERATOR)
    headers = auth_header(client, "owner@example.com")
    team_id = client.post(
        _teams_url(org.id),
        headers=headers,
        json={"name": "Ops", "membership_ids": [str(ops_membership.id)]},
    ).json()["id"]

    assert (
        client.delete(f"/api/v1/organizations/{org.id}/members/{ops_membership.id}", headers=headers).status_code
        == 204
    )
    team = client.get(f"{_teams_url(org.id)}/{team_id}", headers=headers).json()
    assert team["member_count"] == 0


def test_rename_and_delete_team(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    team_id = client.post(
        _teams_url(org.id), headers=headers, json={"name": "Old", "description": "x"}
    ).json()["id"]

    renamed = client.patch(
        f"{_teams_url(org.id)}/{team_id}",
        headers=headers,
        json={"name": "New", "description": None},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "New"
    assert renamed.json()["description"] is None

    assert client.delete(f"{_teams_url(org.id)}/{team_id}", headers=headers).status_code == 204
    assert client.get(f"{_teams_url(org.id)}/{team_id}", headers=headers).status_code == 404


def test_operator_reads_but_cannot_manage_teams(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    ops = create_user(db_session, email="ops@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, ops, OrganizationRole.OPERATOR)
    owner_headers = auth_header(client, "owner@example.com")
    team_id = client.post(_teams_url(org.id), headers=owner_headers, json={"name": "Ops"}).json()["id"]

    headers = auth_header(client, "ops@example.com")
    assert client.get(_teams_url(org.id), headers=headers).status_code == 200
    assert client.get(f"{_teams_url(org.id)}/{team_id}", headers=headers).status_code == 200
    assert client.post(_teams_url(org.id), headers=headers, json={"name": "Mine"}).status_code == 403
    assert client.delete(f"{_teams_url(org.id)}/{team_id}", headers=headers).status_code == 403


def test_viewer_cannot_read_teams(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    viewer = create_user(db_session, email="viewer@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)

    headers = auth_header(client, "viewer@example.com")
    assert client.get(_teams_url(org.id), headers=headers).status_code == 403


def test_team_in_other_org_is_not_found(client: TestClient, db_session: Session) -> None:
    owner_a = create_user(db_session, email="owner-a@example.com")
    owner_b = create_user(db_session, email="owner-b@example.com")
    org_a, _ = create_org_with_owner(db_session, owner_a, slug="org-a")
    org_b, _ = create_org_with_owner(db_session, owner_b, slug="org-b")
    team_b = client.post(
        _teams_url(org_b.id), headers=auth_header(client, "owner-b@example.com"), json={"name": "B"}
    ).json()["id"]

    headers = auth_header(client, "owner-a@example.com")
    response = client.get(f"{_teams_url(org_a.id)}/{team_b}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "team_not_found"
