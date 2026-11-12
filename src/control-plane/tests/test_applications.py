"""Application identities: CRUD, scoped credentials, agent minting and the data-plane policy snapshot."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.applications.dependencies import get_data_plane_api
from app.applications.models import ApplicationCredential
from app.applications.streams import StreamPatternError, stream_matches, validate_stream_pattern
from app.core.config import get_settings
from app.devices.tokens import hash_token
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user
from tests.test_deployments import _register_device

INTERNAL = {"X-Data-Plane-Internal-Token": get_settings().data_plane_internal_token}


def _org(client: TestClient, db_session: Session, email: str = "owner@example.com"):
    owner = create_user(db_session, email=email)
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, email)


def _app(client: TestClient, org, headers, slug: str = "energy-collector", **permissions) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org.id}/applications",
        headers=headers,
        json={"slug": slug, "name": slug.title(), "permissions": permissions},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _policy(client: TestClient) -> dict:
    response = client.get("/internal/data-plane/policy", headers=INTERNAL)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("pattern", "ok"),
    [
        ("energy.power", True),
        ("energy.*", True),
        ("energy.**", True),
        ("*.power", True),
        ("**", False),
        ("energy.**.x", False),
        ("Energy.power", False),
        ("energy..power", False),
        ("meteor.lifecycle", False),
        ("a." * 8 + "a", False),
        ("", False),
    ],
)
def test_stream_pattern_validation(pattern: str, ok: bool) -> None:
    if ok:
        assert validate_stream_pattern(pattern) == pattern
    else:
        with pytest.raises(StreamPatternError):
            validate_stream_pattern(pattern)


def test_stream_matching() -> None:
    assert stream_matches("energy.*", "energy.power")
    assert not stream_matches("energy.*", "energy.power.l1")
    assert stream_matches("energy.**", "energy.power.l1")
    assert not stream_matches("energy.**", "energy")
    assert not stream_matches("energy.power", "energy.voltage")


def test_application_crud_and_validation(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    app = _app(client, org, headers, publish=["energy.*"], read=["energy.**"])
    assert app["permissions"]["publish"] == ["energy.*"]
    assert app["permissions"]["receive_commands"] is False

    base = f"/api/v1/organizations/{org.id}/applications"
    assert client.post(base, headers=headers, json={"slug": "energy-collector", "name": "x"}).status_code == 409
    assert client.post(base, headers=headers, json={"slug": "Bad Slug", "name": "x"}).status_code == 422
    bad = client.post(base, headers=headers, json={"slug": "other", "name": "x", "permissions": {"publish": ["**"]}})
    assert bad.status_code == 422
    unknown = client.post(
        base, headers=headers, json={"slug": "other", "name": "x", "permissions": {"send_commands": ["ghost"]}}
    )
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["error"]["code"] == "unknown_command_target"

    patched = client.patch(
        f"{base}/{app['id']}", headers=headers, json={"permissions": {"subscribe": ["temperature.room"]}}
    )
    assert patched.status_code == 200
    assert patched.json()["permissions"]["publish"] == []
    assert [item["slug"] for item in client.get(base, headers=headers).json()] == ["energy-collector"]

    assert client.delete(f"{base}/{app['id']}", headers=headers).status_code == 204
    assert client.get(f"{base}/{app['id']}", headers=headers).status_code == 404


def test_roles_and_org_isolation(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    app = _app(client, org, headers)
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    viewer_headers = auth_header(client, "viewer@example.com")
    base = f"/api/v1/organizations/{org.id}/applications"
    assert client.get(base, headers=viewer_headers).status_code == 200
    assert client.post(base, headers=viewer_headers, json={"slug": "nope", "name": "x"}).status_code == 403
    assert (
        client.post(f"{base}/{app['id']}/credentials", headers=viewer_headers, json={"name": "x"}).status_code == 403
    )

    other_org, other_headers = _org(client, db_session, email="other@example.com")
    assert client.get(f"{base}/{app["id"]}", headers=other_headers).status_code in {403, 404}
    cross = client.get(f"/api/v1/organizations/{other_org.id}/applications/{app['id']}", headers=other_headers)
    assert cross.status_code == 404


def test_credential_lifecycle_and_policy_snapshot(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    app = _app(client, org, headers, publish=["energy.*"])
    base = f"/api/v1/organizations/{org.id}/applications/{app['id']}/credentials"

    created = client.post(base, headers=headers, json={"name": "cloud worker", "environment": "staging"})
    assert created.status_code == 201, created.text
    secret = created.json()
    assert secret["token"].startswith("mca_")
    assert secret["device_id"] is None

    listed = client.get(base, headers=headers).json()
    assert len(listed) == 1
    assert "token" not in listed[0] and "token_hash" not in listed[0]

    policy = _policy(client)
    assert [item["slug"] for item in policy["applications"]] == ["energy-collector"]
    assert policy["applications"][0]["permissions"]["publish"] == ["energy.*"]
    assert len(policy["credentials"]) == 1
    entry = policy["credentials"][0]
    assert entry["token_hash"] == hash_token(secret["token"])
    assert entry["environment"] == "staging"
    assert entry["organization_id"] == str(org.id)

    # 304 when nothing changed.
    unchanged = client.get(
        "/internal/data-plane/policy", headers={**INTERNAL, "If-None-Match": f'"{policy["version"]}"'}
    )
    assert unchanged.status_code == 304

    rotated = client.post(f"{base}/{secret['id']}/rotate", headers=headers).json()
    assert rotated["token"] != secret["token"]
    policy = _policy(client)
    assert [item["token_hash"] for item in policy["credentials"]] == [hash_token(rotated["token"])]

    assert client.post(f"{base}/{secret['id']}/revoke", headers=headers).status_code == 200
    assert _policy(client)["credentials"] == []
    assert client.post(f"{base}/{secret['id']}/rotate", headers=headers).status_code == 409


def test_policy_leaves_out_expired_disabled_and_requires_token(client: TestClient, db_session: Session) -> None:
    assert client.get("/internal/data-plane/policy").status_code == 401
    assert client.get("/internal/data-plane/policy", headers={"X-Data-Plane-Internal-Token": "no"}).status_code == 401

    org, headers = _org(client, db_session)
    app = _app(client, org, headers)
    base = f"/api/v1/organizations/{org.id}/applications/{app['id']}"
    secret = client.post(f"{base}/credentials", headers=headers, json={"name": "c"}).json()

    row = db_session.get(ApplicationCredential, uuid.UUID(secret["id"]))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert _policy(client)["credentials"] == []

    row.expires_at = datetime.now(UTC) + timedelta(days=1)
    db_session.commit()
    assert len(_policy(client)["credentials"]) == 1
    client.patch(base, headers=headers, json={"enabled": False})
    policy = _policy(client)
    assert policy["applications"] == [] and policy["credentials"] == []


def _deploy_bound(client: TestClient, org, headers, device_id: str, *, application: str | None) -> None:
    spec = {"container_name": "collector", "image": "example/collector:1"}
    if application:
        spec["meteorcloud"] = {"application": application}
    release = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={"name": "collector", "version": f"1.0.{uuid.uuid4().hex[:6]}", "kind": "docker", "spec": spec},
    )
    assert release.status_code == 201, release.text
    deployed = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release.json()["id"], "target": {"device_ids": [device_id]}},
    )
    assert deployed.status_code == 201, deployed.text


def test_agent_mints_device_scoped_credential_for_bound_workload(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    app = _app(client, org, headers, publish=["energy.*"])
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "t"}
    ).json()["token"]
    reg = _register_device(client, token, name="pi-001", mac="aa:bb:cc:dd:ee:01")
    device_headers = {"Authorization": f"Bearer {reg['device_token']}"}
    mint = "/api/v1/agent/applications/credentials"

    # No bound workload yet: a device cannot mint tokens for arbitrary applications.
    assert client.post(mint, headers=device_headers, json={"workload_key": "collector"}).status_code == 404
    _deploy_bound(client, org, headers, reg["device_id"], application=None)
    assert client.post(mint, headers=device_headers, json={"workload_key": "collector"}).status_code == 404

    _deploy_bound(client, org, headers, reg["device_id"], application="energy-collector")
    desired = client.get("/api/v1/agent/software", headers=device_headers)
    assert desired.status_code == 200, desired.text
    specs = [item["spec"] for item in desired.json()]
    assert specs[0]["meteorcloud"] == {"application": "energy-collector", "environment": "production"}

    first = client.post(mint, headers=device_headers, json={"workload_key": "collector"})
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["application"] == "energy-collector"
    assert body["device_id"] == reg["device_id"]
    assert body["organization_id"] == str(org.id)
    assert body["token"].startswith("mca_")

    policy = _policy(client)
    assert [(c["device_id"], c["application_id"]) for c in policy["credentials"]] == [
        (reg["device_id"], app["id"])
    ]

    # Rotation: the new token is live, the old one only survives the grace period.
    second = client.post(mint, headers=device_headers, json={"workload_key": "collector"}).json()
    assert second["token"] != body["token"]
    old = db_session.get(ApplicationCredential, uuid.UUID(body["credential_id"]))
    db_session.refresh(old)
    assert old.expires_at <= datetime.now(UTC) + timedelta(minutes=11)
    assert len(_policy(client)["credentials"]) == 2

    # Disabling the device removes its application credentials from the snapshot.
    disabled = client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/disable", headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert _policy(client)["credentials"] == []

    assert client.post(mint, json={"workload_key": "collector"}).status_code == 401


def test_unbound_release_spec_is_unchanged(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    release = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={"name": "web", "version": "1", "kind": "docker", "spec": {"container_name": "web", "image": "nginx"}},
    ).json()
    assert "meteorcloud" not in release["spec"]
    bad = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "web",
            "version": "2",
            "kind": "docker",
            "spec": {"container_name": "web", "image": "nginx", "meteorcloud": {"application": "Not Valid"}},
        },
    )
    assert bad.status_code in {400, 422}


class _FakeDataPlane:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_command(self, body: dict) -> dict:
        self.created.append(body)
        return {"id": "11111111-1111-1111-1111-111111111111", "state": "PENDING", **body}

    def get_command(self, *, organization_id, command_id):
        for body in self.created:
            if body["organization_id"] == str(organization_id):
                return {"id": str(command_id), "state": "SUCCEEDED", **body}
        return None


def test_commands_are_authorized_here_and_forwarded(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    app = _app(client, org, headers, receive_commands=True)
    base = f"/api/v1/organizations/{org.id}/applications/{app['id']}/commands"

    # Not configured: explicit 503, nothing pretends to work.
    assert client.post(base, headers=headers, json={"name": "restart"}).status_code == 503

    fake = _FakeDataPlane()
    client.app.dependency_overrides[get_data_plane_api] = lambda: fake
    sent = client.post(base, headers=headers, json={"name": "restart", "payload": {"delay": 1}})
    assert sent.status_code == 202, sent.text
    assert fake.created[0]["organization_id"] == str(org.id)
    assert fake.created[0]["application_id"] == app["id"]
    assert fake.created[0]["issued_by"].startswith("user:")
    fetched = client.get(f"{base}/{sent.json()['id']}", headers=headers)
    assert fetched.status_code == 200 and fetched.json()["state"] == "SUCCEEDED"

    # A device from another organization cannot be targeted.
    assert (
        client.post(base, headers=headers, json={"name": "restart", "device_id": str(uuid.uuid4())}).status_code
        == 404
    )
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    assert (
        client.post(base, headers=auth_header(client, "viewer@example.com"), json={"name": "restart"}).status_code
        == 403
    )


def test_reserved_streams_can_be_received_but_not_published(client: TestClient, db_session: Session) -> None:
    org, headers = _org(client, db_session)
    app = _app(client, org, headers, subscribe=["meteor.lifecycle"], read=["meteor.*"])
    assert app["permissions"]["subscribe"] == ["meteor.lifecycle"]
    bad = client.post(
        f"/api/v1/organizations/{org.id}/applications",
        headers=headers,
        json={"slug": "spoofer", "name": "x", "permissions": {"publish": ["meteor.lifecycle"]}},
    )
    assert bad.status_code == 422


def test_broker_hooks_leave_application_logins_to_the_data_plane(client: TestClient) -> None:
    mqtt_internal = {"X-MQTT-Internal-Token": get_settings().mqtt_internal_token}
    auth = client.post(
        "/internal/mqtt/authenticate", headers=mqtt_internal, json={"username": "app_1234", "password": "mca_x"}
    )
    assert auth.json()["result"] == "ignore"
    acl = client.post(
        "/internal/mqtt/authorize",
        headers=mqtt_internal,
        json={"username": "app_1234", "action": "publish", "topic": "devices/x/status"},
    )
    assert acl.json()["result"] == "ignore"
    unknown = client.post(
        "/internal/mqtt/authenticate", headers=mqtt_internal, json={"username": "someone", "password": "x"}
    )
    assert unknown.json()["result"] == "deny"
