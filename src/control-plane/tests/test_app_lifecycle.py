"""Secrets, volumes, redeploy, newer images, quota and erasing of the org registry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.registry.catalog import CatalogService, pin_tag
from app.registry.models import ContainerImage, RegistryNamespace
from app.registry.router import get_registry_service
from app.registry.service import RegistryService
from app.releases.models import Release, ReleaseSecret
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_app_run import _run, ready  # noqa: F401
from tests.test_registry import _access, _token, registry, registry_settings  # noqa: F401
from tests.test_registry_device_pull import DIGEST_A, _device, fake  # noqa: F401


def _apps(client: TestClient, org, headers, device_id: str):
    response = client.get(f"/api/v1/organizations/{org.id}/devices/{device_id}/apps/deployed", headers=headers)
    assert response.status_code == 200, response.text
    return response


def _redeploy(client: TestClient, org, headers, device_id: str, **body):
    url = f"/api/v1/organizations/{org.id}/devices/{device_id}/apps/web/redeploy"
    return client.post(url, headers=headers, json=body)


def _secrets(client: TestClient, device_headers, release_id: str, key: str = "web"):
    body = {"workload_key": key, "release_id": release_id}
    return client.post("/api/v1/agent/software/secrets", headers=device_headers, json=body)


def test_secrets_reach_only_the_device(registry: TestClient, db_session: Session, ready) -> None:  # noqa: F811
    org, headers, device_id, device_headers = ready
    _, stranger = _device(registry, org, headers, "pi2", "aa:bb:cc:dd:ee:09")
    run = _run(
        registry, org, headers, device_id, env={"A": "1"}, secret_env={"KEY": "s3cret"},
        volumes=["data:/data"], resources={"memory_mb": 128},
    )  # fmt: skip
    assert run.status_code == 201, run.text
    release_id = run.json()["release_id"]
    assert "s3cret" not in run.text
    (item,) = registry.get("/api/v1/agent/software", headers=device_headers).json()
    assert item["spec"]["secret_env"] == ["KEY"]
    assert item["spec"]["volumes"] == ["data:/data"]
    assert item["spec"]["resources"]["memory_mb"] == 128
    assert "s3cret" not in str(item)
    apps = _apps(registry, org, headers, device_id)
    assert apps.json()[0]["spec"]["secret_env"] == ["KEY"]
    assert "s3cret" not in apps.text
    row = db_session.scalar(select(ReleaseSecret))
    assert b"s3cret" not in row.sealed_value

    answer = _secrets(registry, device_headers, release_id)
    assert answer.json() == {"env": {"KEY": "s3cret"}}
    assert answer.headers["cache-control"] == "no-store"
    assert _secrets(registry, stranger, release_id).status_code == 404
    assert _secrets(registry, device_headers, release_id, "other").status_code == 404
    assert _secrets(registry, headers, release_id).status_code == 401


def test_redeploy_keeps_what_is_not_changed(registry: TestClient, db_session: Session, ready, fake) -> None:  # noqa: F811
    org, headers, device_id, device_headers = ready
    first = _run(registry, org, headers, device_id, env={"A": "1"}, secret_env={"KEY": "one", "OLD": "x"}).json()
    response = _redeploy(registry, org, headers, device_id, ports=["9090:8080"], secret_env={"NEW": "two", "OLD": None})
    assert response.status_code == 201, response.text
    second = response.json()
    (item,) = registry.get("/api/v1/agent/software", headers=device_headers).json()
    assert (item["spec"]["ports"], item["spec"]["env"]) == (["9090:8080"], {"A": "1"})
    assert item["spec"]["image_digest"] == DIGEST_A
    assert _secrets(registry, device_headers, second["release_id"]).json()["env"] == {"KEY": "one", "NEW": "two"}
    # The first never came up, so it is no rollback target and its secrets stay away.
    assert _secrets(registry, device_headers, first["release_id"]).status_code == 404
    clash = _redeploy(registry, org, headers, device_id, env={"KEY": "plain"})
    assert clash.status_code == 422, clash.text
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    assert _redeploy(registry, org, auth_header(registry, "viewer@example.com"), device_id).status_code == 403


def test_a_moved_tag_and_a_newer_tag_are_offered(registry: TestClient, db_session: Session, ready, fake) -> None:  # noqa: F811
    org, headers, device_id, _ = ready
    registry.get(f"/api/v1/organizations/{org.id}/registry", headers=headers)  # claims the namespace
    catalog = CatalogService(db_session, registry_settings(), fake)
    catalog.reconcile()
    assert _run(registry, org, headers, device_id).status_code == 201
    (app,) = _apps(registry, org, headers, device_id).json()
    assert (app["repository"], app["tag"], app["tag_moved"], app["newer_tag"]) == ("team/web", "1.0", False, None)
    fake.push("acme/team/web", "1.0", seed="b")
    catalog.reconcile()
    (app,) = _apps(registry, org, headers, device_id).json()
    assert app["tag_moved"] is True
    # The image that runs is still there for a rollback.
    assert fake.images["acme/team/web"][pin_tag(DIGEST_A)] == DIGEST_A
    assert _redeploy(registry, org, headers, device_id).status_code == 201
    (app,) = _apps(registry, org, headers, device_id).json()
    assert app["tag_moved"] is False
    tags = set(db_session.scalars(select(ContainerImage.tag)))
    assert tags == {"1.0"}


@pytest.mark.parametrize(
    "volume", ["/etc:/host", "data", "data:relative", "data:/a/../b", "data:/", "data:/run/secrets"]
)
def test_volumes_are_never_device_paths(registry: TestClient, ready, volume: str) -> None:  # noqa: F811
    org, headers, device_id, _ = ready
    assert _run(registry, org, headers, device_id, volumes=[volume]).status_code == 422


def test_secret_values_are_one_line_and_not_echoed(registry: TestClient, ready) -> None:  # noqa: F811
    org, headers, device_id, _ = ready
    response = _run(registry, org, headers, device_id, secret_env={"KEY": "hunter2\nB=1"})
    assert response.status_code == 422
    assert "hunter2" not in response.text


def test_releases_cannot_name_secrets_without_values(registry: TestClient, ready) -> None:  # noqa: F811
    org, headers, _, _ = ready
    spec = {"container_name": "x", "image": "nginx", "secret_env": ["KEY"]}
    response = registry.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={"name": "x", "version": "1", "kind": "docker", "spec": spec},
    )
    assert response.status_code == 422


def test_a_deleted_organization_leaves_no_images(registry: TestClient, db_session: Session, ready, fake) -> None:  # noqa: F811
    org, headers, _, _ = ready
    registry.get(f"/api/v1/organizations/{org.id}/registry", headers=headers)  # claims the namespace
    fake.push("unknown/app", "1")
    db_session.get(RegistryNamespace, "acme").organization_id = None
    db_session.flush()
    CatalogService(db_session, registry_settings(), fake).reconcile()
    assert not fake.images["acme/team/web"]
    assert fake.images["unknown/app"]
    assert db_session.scalar(select(Release.id).where(Release.organization_id == org.id)) is None


def test_a_full_quota_stops_pushes_only(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    ready,  # noqa: F811
    fake,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, headers, _, _ = ready
    token = _token(registry, org, headers)["token"]
    registry.get(f"/api/v1/organizations/{org.id}/registry", headers=headers)  # claims the namespace
    CatalogService(db_session, registry_settings(), fake).reconcile()
    db_session.scalar(select(ContainerImage)).size_bytes = 100
    db_session.flush()
    scope = "repository:acme/team/web:pull,push"
    for quota, actions in ((101, ["pull", "push"]), (100, ["pull"]), (0, ["pull", "push"])):
        registry.app.dependency_overrides[get_registry_service] = lambda q=quota: RegistryService(
            db_session, registry_settings(registry_org_quota_bytes=q)
        )
        assert _access(registry, db_session, token, scope) == {"acme/team/web": actions}
