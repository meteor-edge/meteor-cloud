"""Token auth against a real ``registry:3`` container (REGISTRY_TEST=1, needs Docker)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from urllib.parse import urljoin

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.distribution_registry import DistributionRegistry
from app.core.config import get_settings
from app.registry.catalog import CatalogService
from app.registry.keys import RegistryKeys
from tests.test_registry import _login, _setup, _token, registry, registry_settings  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("REGISTRY_TEST") != "1", reason="set REGISTRY_TEST=1 (needs Docker)")


@pytest.fixture
def distribution(db_session: Session, tmp_path):
    settings = registry_settings(object_storage_provider="filesystem")
    for name, content in DistributionRegistry(settings).config_files(RegistryKeys(db_session, settings).jwks()).items():
        (tmp_path / name).write_text(content)
    tmp_path.chmod(0o755)
    db_session.commit()
    # Nothing listens there: notifications are tested separately.
    name = f"meteorcloud-registry-test-{uuid.uuid4().hex[:8]}"
    run = ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::5000"]
    subprocess.run([*run, "-v", f"{tmp_path}:/etc/distribution:ro", "registry:3"], check=True, capture_output=True)
    try:
        port = subprocess.run(["docker", "port", name, "5000"], check=True, capture_output=True, text=True).stdout
        base = f"http://{port.strip().splitlines()[0]}"
        for _ in range(100):
            try:
                if httpx.get(f"{base}/v2/").status_code == 401:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        yield base
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def _push(base: str, repository: str, headers: dict[str, str]) -> int:
    """Push a one-blob image the way a client does. Returns the last status."""
    blob = b'{"os": "linux", "architecture": "arm64", "variant": "v8"}'
    digest = f"sha256:{hashlib.sha256(blob).hexdigest()}"
    start = httpx.post(f"{base}/v2/{repository}/blobs/uploads/", headers=headers)
    if start.status_code != 202:
        return start.status_code
    location = urljoin(base, start.headers["Location"])
    put = httpx.put(f"{location}&digest={digest}", headers=headers, content=blob)
    assert put.status_code == 201, put.text
    media = "application/vnd.oci.image.manifest.v1+json"
    descriptor = {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": digest, "size": len(blob)}
    manifest = json.dumps({"schemaVersion": 2, "mediaType": media, "config": descriptor, "layers": []})
    return httpx.put(
        f"{base}/v2/{repository}/manifests/1", headers={**headers, "Content-Type": media}, content=manifest
    ).status_code


def test_registry_enforces_our_tokens(registry: TestClient, db_session: Session, distribution: str) -> None:  # noqa: F811
    org, headers = _setup(registry, db_session)
    challenge = httpx.get(f"{distribution}/v2/acme/app/tags/list").headers["WWW-Authenticate"]
    assert 'realm="https://example.com/api/v1/registry/token"' in challenge
    assert 'scope="repository:acme/app:pull"' in challenge

    def bearer(secret: str, repository: str) -> dict[str, str]:
        token = _login(registry, secret, f"repository:{repository}:pull,push").json()["token"]
        # Without the Accept header the registry answers 404 for an OCI manifest.
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.oci.image.manifest.v1+json"}

    push = _token(registry, org, headers)["token"]
    assert _push(distribution, "acme/app", bearer(push, "acme/app")) == 201
    tags = httpx.get(f"{distribution}/v2/acme/app/tags/list", headers=bearer(push, "acme/app"))
    assert tags.json() == {"name": "acme/app", "tags": ["1"]}

    pull = _token(registry, org, headers, access="pull")["token"]
    assert httpx.get(f"{distribution}/v2/acme/app/manifests/1", headers=bearer(pull, "acme/app")).status_code == 200
    assert _push(distribution, "acme/app", bearer(pull, "acme/app")) == 401
    assert _push(distribution, "other/app", bearer(push, "other/app")) == 401
    assert httpx.get(f"{distribution}/v2/_catalog", headers=bearer(push, "acme/app")).status_code == 401

    stranger_org, stranger_headers = _setup(registry, db_session, slug="other", email="other@example.com")
    stranger = _token(registry, stranger_org, stranger_headers)["token"]
    refused = httpx.get(f"{distribution}/v2/acme/app/manifests/1", headers=bearer(stranger, "acme/app"))
    assert refused.status_code == 401


def test_catalog_reads_the_registry(registry: TestClient, db_session: Session, distribution: str) -> None:  # noqa: F811
    org, headers = _setup(registry, db_session)
    secret = _token(registry, org, headers)["token"]
    token = _login(registry, secret, "repository:acme/team/app:pull,push").json()["token"]
    assert _push(distribution, "acme/team/app", {"Authorization": f"Bearer {token}"}) == 201

    settings = registry_settings(registry_internal_url=distribution)
    catalog = CatalogService(db_session, settings)
    assert catalog.reconcile() == 1
    base = f"/api/v1/organizations/{org.id}/registry/images"
    assert [i["repository"] for i in registry.get(base, headers=headers).json()["items"]] == ["team/app"]
    (tag,) = registry.get(f"{base}/tags", headers=headers, params={"repository": "team/app"}).json()["items"]
    assert tag["tag"] == "1"
    assert tag["digest"] == catalog.registry.digest("acme/team/app", "1")
    (platform,) = tag["platforms"]
    assert (platform["os"], platform["architecture"], platform["variant"]) == ("linux", "arm64", "v8")
    assert tag["size_bytes"] == platform["size_bytes"] > 0
    assert catalog.registry.manifest("acme/team/app", "nope") is None

    registry.app.dependency_overrides[get_settings] = lambda: settings
    assert registry.delete(base, headers=headers, params={"repository": "team/app", "tag": "1"}).status_code == 204
    assert catalog.registry.list_tags("acme/team/app") == []
    assert registry.get(base, headers=headers).json()["total"] == 0
    # Reconciling again does not bring it back, and an empty repository is not an error.
    catalog.reconcile()
    assert registry.get(base, headers=headers).json()["total"] == 0
