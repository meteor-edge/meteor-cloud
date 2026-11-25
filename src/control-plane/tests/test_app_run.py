"""POST /devices/{id}/apps/run."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.releases.models import Release
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_registry import HOST, _setup, registry  # noqa: F401
from tests.test_registry_catalog import FakeRegistry
from tests.test_registry_device_pull import DIGEST_A, _device, _pull_token, fake  # noqa: F401


def _run(client: TestClient, org, headers, device_id: str, **body):
    return client.post(
        f"/api/v1/organizations/{org.id}/devices/{device_id}/apps/run",
        headers=headers,
        json={"image": "team/web:1.0", **body},
    )


@pytest.fixture
def ready(registry: TestClient, db_session: Session, fake: FakeRegistry):  # noqa: F811
    org, headers = _setup(registry, db_session)
    fake.push("acme/team/web", "1.0")
    device_id, device_headers = _device(registry, org, headers)
    return org, headers, device_id, device_headers


def test_run_creates_a_release_and_deploys_it(registry: TestClient, ready) -> None:  # noqa: F811
    org, headers, device_id, device_headers = ready
    response = _run(registry, org, headers, device_id, env={"A": "1"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["workload_key"] == "web"
    assert body["deployment"]["status"] == "in_progress"
    assert [d["status"] for d in body["deployment"]["devices"]] == ["pending"]
    (item,) = registry.get("/api/v1/agent/software", headers=device_headers).json()
    assert item["spec"]["image"] == f"{HOST}/acme/team/web:1.0"
    assert item["spec"]["image_digest"] == DIGEST_A
    assert (item["spec"]["ports"], item["spec"]["env"]) == (["8080:8080"], {"A": "1"})
    assert _pull_token(registry, device_headers).status_code == 200

    again = _run(registry, org, headers, device_id)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "app_exists")
    second = _run(registry, org, headers, device_id, name="web-b", ports=["8081:8080"])
    assert second.status_code == 201, second.text


def test_second_run_of_a_tag_gets_the_next_version(registry: TestClient, db_session: Session, ready) -> None:  # noqa: F811
    org, headers, device_id, _ = ready
    other, _ = _device(registry, org, headers, "pi2", "aa:bb:cc:dd:ee:09")
    assert _run(registry, org, headers, device_id).status_code == 201
    assert _run(registry, org, headers, other).status_code == 201
    assert sorted(db_session.scalars(select(Release.version))) == ["1.0", "1.0-2"]


def test_a_refused_run_leaves_nothing_behind(registry: TestClient, db_session: Session, ready) -> None:  # noqa: F811
    org, headers, device_id, _ = ready
    assert _run(registry, org, headers, device_id).status_code == 201
    conflict = _run(registry, org, headers, device_id, name="other")  # host port 8080 is taken
    assert conflict.status_code in (409, 422), conflict.text
    no_port = _run(registry, org, headers, device_id, name="other", ports=[], endpoint={})
    assert no_port.json()["error"]["code"] == "endpoint_port_mismatch"
    missing = _run(registry, org, headers, device_id, name="other", image="nope:1")
    assert missing.json()["error"]["code"] == "image_not_found"
    assert len(db_session.scalars(select(Release)).all()) == 1


def test_permissions(registry: TestClient, db_session: Session, ready) -> None:  # noqa: F811
    org, _, device_id, _ = ready
    results = {}
    for role in (OrganizationRole.VIEWER, OrganizationRole.OPERATOR):
        email = f"{role.value}@example.com"
        add_member(db_session, org, create_user(db_session, email=email), role)
        results[role.value] = _run(registry, org, auth_header(registry, email), device_id).status_code
    # An operator may deploy, and needs no right to manage releases for it.
    assert results == {"viewer": 403, "operator": 201}
    stranger_org, stranger = _setup(registry, db_session, slug="other", email="other@example.com")
    assert _run(registry, org, stranger, device_id).status_code == 404
    assert _run(registry, stranger_org, stranger, device_id).status_code == 404


@pytest.mark.parametrize("name", ["-x", "a b", "a/b", "x" * 64])
def test_bad_names(registry: TestClient, ready, name: str) -> None:  # noqa: F811
    org, headers, device_id, _ = ready
    assert _run(registry, org, headers, device_id, name=name).status_code == 422
