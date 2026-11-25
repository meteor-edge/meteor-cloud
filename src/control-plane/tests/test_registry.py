"""Registry tokens, Docker token auth and namespaces."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.distribution_registry import DistributionRegistry
from app.audit.models import AuditEvent
from app.core.config import Settings, get_settings
from app.devices.rate_limit import InMemoryRateLimiter
from app.registry.keys import RegistryKeys
from app.registry.models import RegistryNamespace, RegistryToken
from app.registry.router import get_registry_rate_limiter
from app.registry.service import parse_scopes
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user

HOST = "registry.example.com"


def registry_settings(**overrides) -> Settings:
    return get_settings().model_copy(
        update={
            "registry_provider": "distribution",
            "registry_public_host": HOST,
            "registry_token_realm": "https://example.com/api/v1/registry/token",
            **overrides,
        }
    )


@pytest.fixture
def registry(client: TestClient) -> TestClient:
    client.app.dependency_overrides[get_settings] = lambda: registry_settings()
    return client


def _setup(client: TestClient, session: Session, slug: str = "acme", email: str = "owner@example.com"):
    owner = create_user(session, email=email)
    org, _ = create_org_with_owner(session, owner, slug=slug)
    return org, auth_header(client, email)


def _token(client: TestClient, org, headers, **body) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org.id}/registry/tokens", headers=headers, json={"name": "laptop", **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, secret: str, *scopes: str, service: str = HOST):
    return client.get("/api/v1/registry/token", params={"service": service, "scope": scopes}, auth=("me", secret))


def _access(client: TestClient, session: Session, secret: str, *scopes: str) -> dict[str, list[str]]:
    response = _login(client, secret, *scopes)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"] == body["access_token"]
    jwk = RegistryKeys(session, registry_settings()).jwks()["keys"][0]
    assert jwt.get_unverified_header(body["token"])["kid"] == jwk["kid"]
    assert "d" not in jwk
    claims = jwt.decode(
        body["token"],
        ECAlgorithm.from_jwk(json.dumps(jwk)),
        algorithms=["ES256"],
        audience=HOST,
        issuer="meteorcloud",
    )
    return {entry["name"]: entry["actions"] for entry in claims["access"]}


def test_personal_token_pushes_and_pulls_in_its_namespace(registry: TestClient, db_session: Session) -> None:
    org, headers = _setup(registry, db_session)
    created = _token(registry, org, headers)
    assert created["token"].startswith("rgt_")
    row = db_session.scalars(select(RegistryToken)).one()
    assert created["token"] not in (row.token_hash, row.token_prefix)

    assert _access(registry, db_session, created["token"], "repository:acme/app:pull,push") == {
        "acme/app": ["pull", "push"]
    }
    assert _access(registry, db_session, created["token"]) == {}  # docker login asks for no scope
    db_session.expire_all()
    assert db_session.scalars(select(RegistryToken)).one().last_used_at is not None
    listed = registry.get(f"/api/v1/organizations/{org.id}/registry/tokens", headers=headers).json()
    assert "token" not in listed[0]


def test_other_namespaces_and_the_catalog_are_never_granted(registry: TestClient, db_session: Session) -> None:
    org, headers = _setup(registry, db_session)
    _setup(registry, db_session, slug="other", email="other@example.com")
    secret = _token(registry, org, headers)["token"]
    scopes = (
        "repository:other/app:pull,push",
        "repository:acme:pull",
        "repository:acme/../other/app:pull",
        "repository:acmex/app:pull",
        "registry:catalog:*",
        "repository:acme/app:*",
        "nonsense",
    )
    assert _access(registry, db_session, secret, *scopes) == {}
    denied = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "image.access")).one()
    assert denied.outcome == "denied"
    assert denied.metadata_["repository"] == "other/app"


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (OrganizationRole.ADMIN, ["pull", "push", "delete"]),
        (OrganizationRole.DEVELOPER, ["pull", "push"]),
        (OrganizationRole.OPERATOR, ["pull"]),
        (OrganizationRole.VIEWER, ["pull"]),
    ],
)
def test_personal_token_follows_the_current_role(
    registry: TestClient, db_session: Session, role: OrganizationRole, expected: list[str]
) -> None:
    """A push token made as a developer only does what the member's role allows now."""
    from app.authorization.roles import get_system_role_id

    org, _ = _setup(registry, db_session)
    user = create_user(db_session, email="dev@example.com")
    membership = add_member(db_session, org, user, OrganizationRole.DEVELOPER)
    secret = _token(registry, org, auth_header(registry, "dev@example.com"))["token"]
    membership.role_id = get_system_role_id(db_session, role.value)
    db_session.commit()
    assert _access(registry, db_session, secret, "repository:acme/app:pull,push,delete") == {"acme/app": expected}


def test_removed_or_inactive_member_cannot_sign_in(registry: TestClient, db_session: Session) -> None:
    org, _ = _setup(registry, db_session)
    user = create_user(db_session, email="dev@example.com")
    membership = add_member(db_session, org, user, OrganizationRole.DEVELOPER)
    secret = _token(registry, org, auth_header(registry, "dev@example.com"))["token"]
    user.is_active = False
    db_session.commit()
    assert _login(registry, secret).status_code == 401
    user.is_active = True
    db_session.delete(membership)
    db_session.commit()
    assert _login(registry, secret).status_code == 401
    reasons = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "registry_token.use"))
    assert {row.metadata_["reason"] for row in reasons} == {"inactive", "not_a_member"}


def test_robot_is_limited_to_its_prefix(registry: TestClient, db_session: Session) -> None:
    org, headers = _setup(registry, db_session)
    robot = _token(registry, org, headers, kind="robot", repository_prefix="ci/*")
    assert robot["repository_prefix"] == "ci"
    assert robot["user_id"] is None
    granted = _access(
        registry,
        db_session,
        robot["token"],
        "repository:acme/ci/app:pull,push,delete repository:acme/cipher:pull",
        "repository:acme/app:push",
    )
    assert granted == {"acme/ci/app": ["pull", "push"]}
    pull_only = _token(registry, org, headers, kind="robot", access="pull")
    assert _access(registry, db_session, pull_only["token"], "repository:acme/app:pull,push") == {"acme/app": ["pull"]}


def test_who_may_create_list_and_revoke(registry: TestClient, db_session: Session) -> None:
    org, headers = _setup(registry, db_session)
    url = f"/api/v1/organizations/{org.id}/registry/tokens"
    users = {}
    for role in (OrganizationRole.DEVELOPER, OrganizationRole.VIEWER):
        add_member(db_session, org, create_user(db_session, email=f"{role.value}@example.com"), role)
        users[role] = auth_header(registry, f"{role.value}@example.com")
    developer, viewer = users[OrganizationRole.DEVELOPER], users[OrganizationRole.VIEWER]

    assert registry.post(url, headers=developer, json={"name": "r", "kind": "robot"}).status_code == 403
    assert registry.post(url, headers=viewer, json={"name": "v"}).status_code == 403
    _token(registry, org, viewer, access="pull")
    mine = _token(registry, org, headers)
    theirs = _token(registry, org, developer)

    assert len(registry.get(url, headers=headers).json()) == 3
    assert [row["id"] for row in registry.get(url, headers=developer).json()] == [theirs["id"]]
    assert registry.post(f"{url}/{mine['id']}/revoke", headers=developer).status_code == 403
    assert registry.post(f"{url}/{theirs['id']}/revoke", headers=headers).json()["revoked_at"]
    assert _login(registry, theirs["token"]).status_code == 401

    _, other_headers = _setup(registry, db_session, slug="other", email="other@example.com")
    assert registry.get(url, headers=other_headers).status_code == 404
    assert registry.post(f"{url}/{mine['id']}/revoke", headers=other_headers).status_code == 404
    actions = set(db_session.scalars(select(AuditEvent.action).where(AuditEvent.resource_type == "registry_token")))
    assert actions == {"registry_token.create", "registry_token.revoke", "registry_token.use"}


def test_bad_credentials_expiry_and_service(registry: TestClient, db_session: Session) -> None:
    org, headers = _setup(registry, db_session)
    anonymous = registry.get("/api/v1/registry/token", params={"service": HOST})
    assert anonymous.status_code == 401
    assert anonymous.headers["WWW-Authenticate"].startswith("Basic")
    assert _login(registry, "rgt_wrong").status_code == 401
    secret = _token(registry, org, headers)["token"]
    assert _login(registry, secret, service="elsewhere").status_code == 400
    row = db_session.scalars(select(RegistryToken)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert _login(registry, secret).status_code == 401
    url = f"/api/v1/organizations/{org.id}/registry/tokens"
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert registry.post(url, headers=headers, json={"name": "x", "expires_at": past}).status_code == 409
    for body in ({"kind": "robot", "repository_prefix": "Bad/../x"}, {"repository_prefix": "ci"}, {"name": " "}):
        assert registry.post(url, headers=headers, json={"name": "x", **body}).status_code == 422


def test_rate_limit(registry: TestClient) -> None:
    # A window this long cannot end between the two requests.
    limiter = InMemoryRateLimiter(limit=1, window_seconds=10**12)
    registry.app.dependency_overrides[get_registry_rate_limiter] = lambda: limiter
    assert _login(registry, "rgt_x").status_code == 401
    assert _login(registry, "rgt_x").status_code == 429


def test_namespace_survives_rename_and_is_never_reused(registry: TestClient, db_session: Session) -> None:
    org, headers = _setup(registry, db_session)
    url = f"/api/v1/organizations/{org.id}"
    assert registry.get(f"{url}/registry", headers=headers).json() == {
        "enabled": True,
        "host": HOST,
        "namespace": "acme",
        "used_bytes": 0,
        "quota_bytes": None,
    }
    assert registry.patch(url, headers=headers, json={"slug": "renamed"}).status_code == 200
    assert registry.get(f"{url}/registry", headers=headers).json()["namespace"] == "acme"

    assert registry.delete(url, headers=headers).status_code == 204
    db_session.expire_all()
    assert db_session.get(RegistryNamespace, "acme").organization_id is None
    # The slug is free again after deletion, but not the old images.
    org2, headers2 = _setup(registry, db_session, email="new@example.com")
    info = registry.get(f"/api/v1/organizations/{org2.id}/registry", headers=headers2).json()
    assert info["namespace"] == "acme-2"
    secret = _token(registry, org2, headers2)["token"]
    assert _access(registry, db_session, secret, "repository:acme/app:pull") == {}


def test_disabled_registry(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    url = f"/api/v1/organizations/{org.id}/registry"
    assert client.get(url, headers=headers).json()["enabled"] is False
    assert client.post(f"{url}/tokens", headers=headers, json={"name": "x"}).status_code == 404
    assert _login(client, "rgt_x").status_code == 404


def test_key_is_reused_then_rotated(db_session: Session) -> None:
    keys = RegistryKeys(db_session, registry_settings())
    first, pem = keys.signing_key()
    assert keys.signing_key()[0] == first
    assert "PRIVATE KEY" in pem
    assert keys.rotate().key_id != first
    assert [key["kid"] for key in keys.jwks()["keys"]] == [keys.signing_key()[0]]


def test_parse_scopes_keeps_ports_and_ignores_the_rest() -> None:
    (scope,) = parse_scopes(["repository:host:5000/a/b:pull,push,pull", "x", "repository::pull"])
    assert (scope.repository, scope.actions) == ("host:5000/a/b", ("pull", "push"))


def test_settings_need_addresses() -> None:
    with pytest.raises(ValidationError, match="REGISTRY_PUBLIC_HOST"):
        Settings(REGISTRY_PROVIDER="distribution", _env_file=None)


@pytest.mark.parametrize(
    ("endpoint", "public", "redirect_disabled"),
    [("", "", False), ("http://minio:9000", "", True), ("https://r2.example", "https://r2.example", False)],
)
def test_distribution_s3_config(endpoint: str, public: str, redirect_disabled: bool) -> None:
    settings = registry_settings(
        object_storage_provider="s3",
        object_storage_endpoint_url=endpoint,
        object_storage_public_endpoint_url=public,
        object_storage_bucket="artifacts",
        object_storage_secret_access_key="do-not-write-me",
    )
    files = DistributionRegistry(settings).config_files({"keys": []})
    assert "do-not-write-me" not in files["config.yml"]
    assert settings.app_secret_key not in files["config.yml"]
    storage = json.loads(files["config.yml"])["storage"]
    assert "filesystem" not in storage  # two drivers make the registry refuse to start
    assert storage["s3"]["bucket"] == "artifacts"
    assert storage["s3"]["rootdirectory"] == "/registry"
    assert ("regionendpoint" in storage["s3"]) == bool(endpoint)
    assert storage["redirect"]["disable"] is redirect_disabled
