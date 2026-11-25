"""Registry images in releases: pinning, platforms and device pulls."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationAppError
from app.deployments.software_models import DeviceSoftware
from app.devices.models import Device
from app.registry import images as registry_images
from app.registry.images import RegistryImages, image_architecture
from app.releases.kinds import DockerComposeSpec, ReleaseKind
from app.releases.models import Release
from tests.test_deployments import _register_device
from tests.test_registry import HOST, _access, _login, _setup, _token, registry, registry_settings  # noqa: F401
from tests.test_registry_catalog import FakeRegistry

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


@pytest.fixture
def fake(registry: TestClient, monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:  # noqa: F811
    class Fake(FakeRegistry):
        def digest(self, repository: str, reference: str) -> str | None:
            found = super().digest(repository, reference)
            return found or (reference if reference in self.images.get(repository, {}).values() else None)

    value = Fake()
    monkeypatch.setattr(registry_images, "registry_client", lambda session, settings: value)
    for name in ("app.releases.service", "app.deployments.service"):
        monkeypatch.setattr(f"{name}.RegistryImages", lambda s, *_: RegistryImages(s, registry_settings(), value))
    return value


def _release(client: TestClient, org, headers, image: str, version: str = "1", **spec):
    return client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "web",
            "version": version,
            "kind": "docker",
            "spec": {"container_name": "web", "image": image, "registry": "meteorcloud", **spec},
        },
    )


def _device(client: TestClient, org, headers, name: str = "pi", mac: str = "aa:bb:cc:dd:ee:01"):
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "t"}
    ).json()["token"]
    registered = _register_device(client, token, name=name, mac=mac)
    return registered["device_id"], {"Authorization": f"Bearer {registered['device_token']}"}


def _deploy(client: TestClient, org, headers, release_id: str, *device_ids: str):
    return client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release_id, "target": {"device_ids": list(device_ids)}},
    )


def _pull_token(client: TestClient, device_headers):
    return client.post("/api/v1/agent/registry/pull-token", headers=device_headers, json={"workload_key": "web"})


@pytest.mark.parametrize(
    "image", ["web:1.0", f"{HOST}/acme/web:1.0", f"web:1.0@{DIGEST_A}", f"{HOST}/acme/web:1.0@{DIGEST_A}"]
)
def test_release_is_pinned_to_the_registry(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
    image: str,
) -> None:
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    response = _release(registry, org, headers, image)
    assert response.status_code == 201, response.text
    spec = response.json()["spec"]
    assert (spec["image"], spec["image_digest"]) == (f"{HOST}/acme/web:1.0", DIGEST_A)


@pytest.mark.parametrize(
    ("image", "code"),
    [
        ("web:2.0", "image_not_found"),
        (f"web@{DIGEST_B}", "image_not_found"),
        (f"{HOST}/other/web:1.0", "invalid_release_spec"),
        (f"{HOST}/acme", "invalid_release_spec"),
        ("../other/web:1.0", "invalid_release_spec"),
        ("evil.example.com:5000/acme/web:1.0", "invalid_release_spec"),
        ("other/web:1.0", "image_not_found"),
        ("Web", "invalid_release_spec"),
    ],
)
def test_release_refuses_images_outside_the_namespace(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
    image: str,
    code: str,
) -> None:
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    fake.push("other/web", "1.0")
    response = _release(registry, org, headers, image)
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)


def test_editing_keeps_the_pin(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    release = _release(registry, org, headers, "web:1.0").json()
    fake.push("acme/web", "1.0", "b")
    spec = {**release["spec"], "env": {"A": "1"}}
    url = f"/api/v1/organizations/{org.id}/releases/{release['id']}"
    edited = registry.patch(url, headers=headers, json={"spec": spec})
    assert edited.status_code == 200, edited.text
    assert edited.json()["spec"]["image_digest"] == DIGEST_A
    fake.down = True
    assert _release(registry, org, headers, "web:1.0", "2").status_code == 503


def test_specs_without_the_registry_keep_their_shape(registry: TestClient, db_session: Session) -> None:  # noqa: F811
    org, headers = _setup(registry, db_session)
    response = registry.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={"name": "x", "version": "1", "kind": "docker", "spec": {"container_name": "x", "image": "nginx:1"}},
    )
    assert "registry" not in response.json()["spec"]


def test_device_pulls_only_what_is_desired(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
) -> None:
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    device_id, device_headers = _device(registry, org, headers)
    _, stranger = _device(registry, org, headers, "pi2", "aa:bb:cc:dd:ee:02")
    assert _pull_token(registry, device_headers).status_code == 404
    release = _release(registry, org, headers, "web:1.0").json()
    deployed = _deploy(registry, org, headers, release["id"], device_id)
    assert deployed.status_code == 201, deployed.text
    assert _pull_token(registry, stranger).status_code == 404
    assert _pull_token(registry, headers).status_code == 401

    issued = _pull_token(registry, device_headers)
    assert issued.status_code == 200, issued.text
    assert issued.json()["registry"] == HOST
    password = issued.json()["password"]
    scopes = ("repository:acme/web:pull,push,delete", "repository:acme/secret:pull", "registry:catalog:*")
    assert _access(registry, db_session, password, *scopes) == {"acme/web": ["pull"]}

    # Cancelled: the same password stops working, and a new one is refused.
    url = f"/api/v1/organizations/{org.id}/deployments/{deployed.json()['id']}/cancel"
    assert registry.post(url, headers=headers).status_code == 200
    assert _login(registry, password, "repository:acme/web:pull").status_code == 401
    assert _pull_token(registry, device_headers).status_code == 404


def test_disabled_device_and_forged_passwords(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    device_id, device_headers = _device(registry, org, headers)
    _deploy(registry, org, headers, _release(registry, org, headers, "web:1.0").json()["id"], device_id)
    password = _pull_token(registry, device_headers).json()["password"]

    # An access token is not a password.
    secret = _token(registry, org, headers)["token"]
    access_token = _login(registry, secret, "repository:acme/web:pull").json()["token"]
    assert _login(registry, access_token, "repository:acme/web:pull").status_code == 401
    head, body, signature = password.split(".")
    for forged in (f"{head}.{body}.{signature[::-1]}", f"{head}.{body}.", "a.b.c", "."):
        assert _login(registry, forged, "repository:acme/web:pull").status_code == 401

    monkeypatch.setattr(registry_images, "PULL_PASSWORD_SECONDS", -60)
    expired = _pull_token(registry, device_headers).json()["password"]
    assert _login(registry, expired, "repository:acme/web:pull").status_code == 401

    device = db_session.get(Device, uuid.UUID(device_id))
    device.is_enabled = False
    db_session.commit()
    assert _login(registry, password, "repository:acme/web:pull").status_code == 401


def test_platform_mismatch(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    from app.registry.catalog import CatalogService

    org, headers = _setup(registry, db_session)
    registry.get(f"/api/v1/organizations/{org.id}/registry", headers=headers)  # claims the namespace
    fake.push("acme/web", "1.0")
    CatalogService(db_session, registry_settings(), fake).reconcile()  # linux/arm64
    release = _release(registry, org, headers, "web:1.0").json()
    ids = {}
    for number, architecture in enumerate(("x86_64", "aarch64", None)):
        device_id, _ = _device(registry, org, headers, f"d{number}", f"aa:bb:cc:dd:ee:1{number}")
        db_session.get(Device, uuid.UUID(device_id)).architecture = architecture
        ids[architecture] = device_id
    db_session.commit()
    refused = _deploy(registry, org, headers, release["id"], ids["x86_64"])
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "image_platform_mismatch"
    assert "linux/arm64" in refused.json()["error"]["message"]
    mixed = _deploy(registry, org, headers, release["id"], *ids.values())
    assert mixed.status_code == 201, mixed.text
    by_device = {d["device_id"]: d["status"] for d in mixed.json()["devices"]}
    assert by_device[ids["x86_64"]] == "skipped"
    assert by_device[ids["aarch64"]] == by_device[ids[None]] == "pending"


def test_platform_check_outlives_the_tag(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    from app.registry.catalog import CatalogService

    org, headers = _setup(registry, db_session)
    registry.get(f"/api/v1/organizations/{org.id}/registry", headers=headers)  # claims the namespace
    fake.push("acme/web", "1.0")
    fake.push("acme/web", "kept")  # the registry still has the old digest after the tag moves
    release = _release(registry, org, headers, "web:1.0").json()
    device_id, _ = _device(registry, org, headers)
    db_session.get(Device, uuid.UUID(device_id)).architecture = "x86_64"
    db_session.commit()
    fake.push("acme/web", "1.0", "b")
    catalog = CatalogService(db_session, registry_settings(), fake)
    catalog.sync_tag(org.id, "acme/web", "web", "1.0")
    db_session.commit()
    refused = _deploy(registry, org, headers, release["id"], device_id)
    assert refused.json()["error"]["code"] == "image_platform_mismatch"
    fake.down = True  # nobody knows
    assert _deploy(registry, org, headers, release["id"], device_id).status_code == 201


def test_platforms_are_read_once_per_deployment(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    release = _release(registry, org, headers, "web:1.0").json()
    ids = [_device(registry, org, headers, f"d{n}", f"aa:bb:cc:dd:ee:2{n}")[0] for n in range(3)]
    for device_id in ids:
        db_session.get(Device, uuid.UUID(device_id)).architecture = "aarch64"
    db_session.commit()
    read = fake.manifest
    calls: list[str] = []
    monkeypatch.setattr(fake, "manifest", lambda repository, ref: calls.append(ref) or read(repository, ref))
    assert _deploy(registry, org, headers, release["id"], *ids).status_code == 201
    assert calls == [DIGEST_A]


@pytest.mark.parametrize(
    ("device", "architecture", "variant", "runs"),
    [
        ("armv6l", "arm", "v7", False),
        ("armv6l", "arm", "v6", True),
        ("armv7l", "arm", "v6", True),
        ("armv7l", "arm", "v7", True),
        ("armv7l", "arm", "v8", False),
        ("armv7l", "arm64", "v8", False),
        ("armv6l", "arm", None, True),  # the image does not say
        ("arm", "arm", "v7", True),  # the device does not say
        ("aarch64", "arm", "v7", True),
        ("aarch64", "arm64", "v8", True),
        ("x86_64", "386", None, True),
        ("x86_64", "arm", "v7", False),
    ],
)
def test_cpu_variants(device: str, architecture: str, variant: str | None, runs: bool) -> None:
    both = {"os": "linux", "architecture": "arm", "variant": "v5"}, {"os": "linux", "architecture": "amd64"}
    platforms = {"sidecar": list(both), "web": [{"os": "linux", "architecture": architecture, "variant": variant}]}
    mismatch = RegistryImages.platform_mismatch(platforms, Device(architecture=device))
    assert (mismatch is None) == runs


def test_architecture_names() -> None:
    assert [image_architecture(a) for a in ("AARCH64", "x86_64", "armv7l", "sparc", None)] == [
        "arm64", "amd64", "arm", None, None,
    ]  # fmt: skip


def test_compose_images_are_pinned_and_pullable(
    registry: TestClient,  # noqa: F811
    db_session: Session,
    fake: FakeRegistry,
) -> None:
    org, headers = _setup(registry, db_session)
    fake.push("acme/web", "1.0")
    fake.push("acme/team/db", "2", seed="b")
    fake.push("acme/other", "1")
    spec = DockerComposeSpec(project_name="shop", registry_images=["web:1.0", f"{HOST}/acme/team/db:2"]).model_dump()
    images = RegistryImages(db_session, registry_settings(), fake)
    pinned = images.pin_all(org.id, spec, None)
    web, db = f"{HOST}/acme/web:1.0@{DIGEST_A}", f"{HOST}/acme/team/db:2@sha256:{'b' * 64}"
    assert pinned["registry_images"] == sorted([web, db])
    fake.push("acme/web", "1.0", seed="c")
    assert images.pin_all(org.id, spec, pinned)["registry_images"] == sorted([web, db])  # an edit keeps the pin
    assert images.pin_all(org.id, pinned, None)["registry_images"] == sorted([web, db])  # as stored
    for bad in ("nope:1", "other.example.com/x:1", f"{HOST}/foreign/web:1"):
        with pytest.raises(ValidationAppError):
            images.pin_all(org.id, {"registry_images": [bad]}, None)

    device_id, device_headers = _device(registry, org, headers)
    release = Release(
        organization_id=org.id, name="shop", version="1", kind=ReleaseKind.DOCKER_COMPOSE, spec=pinned
    )  # fmt: skip
    db_session.add(release)
    db_session.flush()
    db_session.add(
        DeviceSoftware(
            organization_id=org.id, device_id=uuid.UUID(device_id), workload_key="shop", kind="docker_compose",
            desired_release_id=release.id, desired_spec=pinned,
        )
    )  # fmt: skip
    db_session.flush()
    issued = registry.post("/api/v1/agent/registry/pull-token", headers=device_headers, json={"workload_key": "shop"})
    assert issued.status_code == 200, issued.text
    found = RegistryImages(db_session, registry_settings(), fake).pullable_repositories(issued.json()["password"])
    assert found is not None and found[1] == {"acme/web", "acme/team/db"}


def test_compose_platforms_outlive_the_tag(registry: TestClient, db_session: Session, fake: FakeRegistry) -> None:  # noqa: F811
    org, headers = _setup(registry, db_session)
    registry.get(f"/api/v1/organizations/{org.id}/registry", headers=headers)  # claims the namespace
    fake.push("acme/web", "1.0")
    fake.push("acme/db", "2", "b")
    images = RegistryImages(db_session, registry_settings(), fake)
    spec = images.pin_all(org.id, {"registry_images": ["web:1.0", "db:2"]}, None)
    fake.push("acme/db", "2", "c")  # moves; the pin keeps the old digest
    platforms = images.platforms(org.id, spec)
    assert [[p["architecture"] for p in built_for] for built_for in platforms.values()] == [["arm64"], ["arm64"]]
    assert "linux/amd64" in (images.platform_mismatch(platforms, Device(architecture="x86_64")) or "")
    assert images.platform_mismatch(platforms, Device(architecture="aarch64")) is None
