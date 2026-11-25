"""The image list: notifications, reconciliation and the API."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.distribution_registry import DistributionRegistry
from app.audit.models import AuditEvent
from app.ports.registry import ImageManifest, Platform, RegistryUnavailableError
from app.registry.access import events_secret
from app.registry.catalog import CatalogService
from app.registry.models import ContainerImage
from app.registry.router import get_catalog_service
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_registry import _setup, _token, registry, registry_settings  # noqa: F401


class FakeRegistry:
    def __init__(self) -> None:
        self.images: dict[str, dict[str, str]] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise RegistryUnavailableError("The registry is unreachable.")

    def push(self, repository: str, tag: str, seed: str = "a") -> None:
        self.images.setdefault(repository, {})[tag] = f"sha256:{seed * 64}"

    def list_repositories(self) -> list[str]:
        self._check()
        return list(self.images)

    def list_tags(self, repository: str) -> list[str]:
        self._check()
        return list(self.images.get(repository, {}))

    def digest(self, repository: str, reference: str) -> str | None:
        self._check()
        return self.images.get(repository, {}).get(reference)

    def manifest(self, repository: str, reference: str) -> ImageManifest | None:
        digest = self.digest(repository, reference)
        if digest is None:
            return None
        media_type = "application/vnd.oci.image.manifest.v1+json"
        return ImageManifest(digest, media_type, (Platform("linux", "arm64", None, 7),))

    def delete_tag(self, repository: str, tag: str) -> None:
        self._check()
        self.images[repository].pop(tag, None)


@pytest.fixture
def fake(registry: TestClient, db_session: Session) -> FakeRegistry:  # noqa: F811
    value = FakeRegistry()
    registry.app.dependency_overrides[get_catalog_service] = lambda: CatalogService(
        db_session, registry_settings(), value
    )
    return value


def _event(action: str, repository: str, tag: str | None = None, actor: str = "") -> dict:
    return {"action": action, "target": {"repository": repository, "tag": tag}, "actor": {"name": actor}}


def _notify(client: TestClient, *events: dict, secret: str | None = None):
    return client.post(
        "/internal/registry/events",
        headers={"X-Registry-Events-Token": secret or events_secret(registry_settings())},
        json={"events": list(events)},
    )


def _claim(client: TestClient, session: Session, **kwargs):
    org, headers = _setup(client, session, **kwargs)
    assert client.get(f"/api/v1/organizations/{org.id}/registry", headers=headers).json()["namespace"] == org.slug
    return org, headers, f"/api/v1/organizations/{org.id}/registry/images"


def test_push_and_delete_notifications(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    org, headers, base = _claim(registry, db_session)
    token = _token(registry, org, headers, name="builder")
    fake.push("acme/team/app", "1.0")
    assert _notify(registry, _event("push", "acme/team/app", "1.0", token["id"])).status_code == 204
    (tag,) = registry.get(f"{base}/tags", headers=headers, params={"repository": "team/app"}).json()["items"]
    assert (tag["tag"], tag["size_bytes"], tag["pushed_by"]) == ("1.0", 7, "builder")

    fake.push("acme/team/app", "1.0", "b")  # the tag moves
    _notify(registry, _event("push", "acme/team/app", "1.0"))
    (tag,) = registry.get(f"{base}/tags", headers=headers, params={"repository": "team/app"}).json()["items"]
    assert tag["digest"] == "sha256:" + "b" * 64

    fake.images["acme/team/app"].clear()
    _notify(registry, _event("delete", "acme/team/app"))
    assert registry.get(base, headers=headers).json()["total"] == 0


def test_notifications_are_not_trusted(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    _, headers, base = _claim(registry, db_session)
    other, other_headers, _ = _claim(registry, db_session, slug="other", email="other@example.com")
    foreign = _token(registry, other, other_headers)
    fake.push("acme/app", "1")
    assert _notify(registry, _event("push", "acme/app", "1"), secret="wrong").status_code == 401
    assert db_session.scalars(select(ContainerImage)).all() == []
    # Unknown namespaces, odd shapes and tags that do not exist are skipped, not retried for ever.
    odd = [_event("push", "nobody/app", "1"), _event("push", "acme/app", "gone"), {"action": "push"}, {"target": 3}]
    assert _notify(registry, *odd).status_code == 204
    assert _notify(registry, _event("push", "acme/app", "1", foreign["id"])).status_code == 204
    (tag,) = registry.get(f"{base}/tags", headers=headers, params={"repository": "app"}).json()["items"]
    assert tag["pushed_by"] is None
    fake.down = True
    assert _notify(registry, _event("push", "acme/app", "1")).status_code == 503


def test_reconcile_finds_what_notifications_missed(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
) -> None:
    _, headers, base = _claim(registry, db_session)
    fake.push("acme/app", "1")
    fake.push("nobody/app", "1")
    catalog = CatalogService(db_session, registry_settings(), fake)
    assert catalog.reconcile() == 1
    (tag,) = registry.get(f"{base}/tags", headers=headers, params={"repository": "app"}).json()["items"]
    pushed_at = tag["pushed_at"]
    catalog.reconcile()  # nothing changed: the row is left alone
    (tag,) = registry.get(f"{base}/tags", headers=headers, params={"repository": "app"}).json()["items"]
    assert tag["pushed_at"] == pushed_at
    fake.images.clear()  # the registry's storage was emptied
    catalog.reconcile()
    assert registry.get(base, headers=headers).json()["items"] == []


def test_list_search_and_isolation(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    org, headers, base = _claim(registry, db_session)
    _, other_headers, other_base = _claim(registry, db_session, slug="other", email="other@example.com")
    for name in ("web", "worker"):
        fake.push(f"acme/{name}", "1")
        fake.push(f"acme/{name}", "2")
    CatalogService(db_session, registry_settings(), fake).reconcile()
    body = registry.get(base, headers=headers, params={"search": "WORK"}).json()
    assert [(i["repository"], i["tag_count"]) for i in body["items"]] == [("worker", 2)]
    assert registry.get(base, headers=headers, params={"page_size": 1}).json()["total"] == 2
    assert registry.get(other_base, headers=other_headers).json()["items"] == []
    assert registry.get(base, headers=other_headers).status_code == 404
    gone = registry.delete(other_base, headers=other_headers, params={"repository": "web"})
    assert gone.status_code == 404
    assert len(fake.images["acme/web"]) == 2


def test_delete_needs_permission_and_is_audited(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
) -> None:
    org, headers, base = _claim(registry, db_session)
    dev = create_user(db_session, email="dev@example.com")
    add_member(db_session, org, dev, OrganizationRole.DEVELOPER)
    for tag in ("1", "2", "3"):
        fake.push("acme/app", tag)
    CatalogService(db_session, registry_settings(), fake).reconcile()
    refused = registry.delete(base, headers=auth_header(registry, "dev@example.com"), params={"repository": "app"})
    assert refused.status_code == 403
    fake.down = True
    assert registry.delete(base, headers=headers, params={"repository": "app"}).status_code == 503
    fake.down = False
    assert registry.delete(base, headers=headers, params={"repository": "app", "tag": "1"}).status_code == 204
    assert sorted(fake.images["acme/app"]) == ["2", "3"]
    assert registry.delete(base, headers=headers, params={"repository": "app"}).status_code == 204
    assert fake.images["acme/app"] == {}
    events = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "image.delete")).all()
    assert sorted(e.metadata_["tag"] for e in events) == ["1", "2", "3"]


def test_tenant_written_manifests_do_not_break_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DistributionRegistry(registry_settings(), lambda access: "token")
    headers = {"docker-content-digest": "sha256:" + "a" * 64}
    documents = [
        b"not json",
        b"[]",
        b'{"config": "x", "layers": "y"}',
        b'{"manifests": "x"}',
        b'{"manifests": [1, {"platform": 2}, {"platform": {"os": "unknown"}}]}',
        json.dumps({"config": {"digest": "../../x"}, "layers": [{"size": "big"}, 4, {"size": -1}]}).encode(),
    ]
    for document in documents:
        monkeypatch.setattr(client, "_request", lambda *args, _body=document, **kwargs: (200, headers, _body))
        manifest = client.manifest("acme/app", "1")
        assert manifest is not None
        assert manifest.platforms == ()
