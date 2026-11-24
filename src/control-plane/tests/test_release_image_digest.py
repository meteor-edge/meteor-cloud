"""Docker releases are pinned to an image digest when they are saved."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.releases import image_digest
from app.releases.image_digest import DigestResolutionError, parse_reference, resolve_image_digest
from tests.conftest import auth_header, create_org_with_owner, create_user

DIGEST = "sha256:" + "ab" * 32


@pytest.mark.parametrize(
    ("image", "registry", "repository", "tag", "name"),
    [
        ("nginx", "registry-1.docker.io", "library/nginx", "latest", "nginx"),
        ("nginx:1.27-alpine", "registry-1.docker.io", "library/nginx", "1.27-alpine", "nginx"),
        ("grafana/grafana:11.0.0", "registry-1.docker.io", "grafana/grafana", "11.0.0", "grafana/grafana"),
        ("ghcr.io/meteor-edge/app:1.0", "ghcr.io", "meteor-edge/app", "1.0", "ghcr.io/meteor-edge/app"),
        ("registry.example.com:5000/team/app", "registry.example.com:5000", "team/app", "latest",
         "registry.example.com:5000/team/app"),
    ],
)
def test_parse_reference(image: str, registry: str, repository: str, tag: str, name: str) -> None:
    ref = parse_reference(image)
    assert (ref.registry, ref.repository, ref.tag, ref.name) == (registry, repository, tag, name)


def test_reference_with_digest_needs_no_lookup() -> None:
    assert resolve_image_digest(f"nginx@{DIGEST}") == DIGEST


def test_resolve_follows_the_anonymous_token_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_request(url: str, *, method: str, headers: dict[str, str]):
        calls.append((url, headers))
        if url.startswith("https://auth.docker.io/token"):
            return 200, {}, b'{"token": "anon"}'
        if headers.get("Authorization") == "Bearer anon":
            return 200, {"docker-content-digest": DIGEST}, b""
        challenge = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
        return 401, {"www-authenticate": challenge}, b""

    monkeypatch.setattr(image_digest, "_request", fake_request)
    assert resolve_image_digest("nginx:1.27") == DIGEST
    assert calls[0][0] == "https://registry-1.docker.io/v2/library/nginx/manifests/1.27"
    assert "scope=repository%3Alibrary%2Fnginx%3Apull" in calls[1][0]


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost"])
def test_private_registries_are_not_contacted(host: str) -> None:
    with pytest.raises(DigestResolutionError):
        image_digest._require_public_host(f"https://{host}/v2/")


def test_plain_http_is_not_contacted() -> None:
    with pytest.raises(DigestResolutionError):
        image_digest._require_public_host("http://registry.example.com/v2/")


def _org(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, "owner@example.com")


def _create(client: TestClient, org, headers, image: str, version: str = "1.0.0"):
    return client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={"name": "web", "version": version, "kind": "docker", "spec": {"container_name": "web", "image": image}},
    )


def test_release_is_pinned_when_saved(client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "release_resolve_image_digests", True)
    monkeypatch.setattr("app.releases.service.resolve_image_digest", lambda image: DIGEST)
    org, headers = _org(client, db_session)

    created = _create(client, org, headers, "nginx:1.27")
    assert created.status_code == 201, created.text
    assert created.json()["spec"]["image_digest"] == DIGEST

    # Changing other fields keeps the pin without a new lookup.
    monkeypatch.setattr("app.releases.service.resolve_image_digest", lambda image: pytest.fail("looked up again"))
    updated = client.patch(
        f"/api/v1/organizations/{org.id}/releases/{created.json()['id']}",
        headers=headers,
        json={"spec": {"container_name": "web", "image": "nginx:1.27", "ports": ["8080:80"]}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"]["image_digest"] == DIGEST


def test_unresolvable_image_keeps_the_tag_unless_digests_are_required(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "release_resolve_image_digests", True)

    def fail(image: str) -> str:
        raise DigestResolutionError("Registry needs credentials; keeping the tag.")

    monkeypatch.setattr("app.releases.service.resolve_image_digest", fail)
    org, headers = _org(client, db_session)

    kept = _create(client, org, headers, "registry.example.com/private/app:2")
    assert kept.status_code == 201, kept.text
    assert "image_digest" not in kept.json()["spec"]

    monkeypatch.setattr(settings, "release_require_image_digests", True)
    refused = _create(client, org, headers, "registry.example.com/private/app:3", version="2.0.0")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "image_digest_unresolved"
