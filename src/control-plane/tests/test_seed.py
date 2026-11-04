import hashlib
import time

from sqlalchemy import select

from app.artifacts.models import Artifact
from app.devices.models import Device, DeviceGroup, DeviceType
from app.releases.kinds import ReleaseKind
from app.releases.models import Release
from app.tenancy.models import Organization
from scripts.seed import ORG_SLUG, _demo_releases, seed


def test_seed_creates_a_repeatable_demo_fleet(db_session, object_storage):
    seed(db_session, storage=object_storage)
    seed(db_session, storage=object_storage)

    organization = db_session.scalar(select(Organization).where(Organization.slug == ORG_SLUG))
    assert organization is not None

    device_names = db_session.scalars(
        select(Device.name)
        .where(Device.organization_id == organization.id)
        .order_by(Device.name)
    ).all()
    assert device_names == ["lab-prototype-01", "store-display-02", "store-gateway-01"]

    assert db_session.scalars(
        select(DeviceType).where(DeviceType.organization_id == organization.id)
    ).all()
    assert db_session.scalars(
        select(DeviceGroup).where(DeviceGroup.organization_id == organization.id)
    ).all()

    releases = db_session.scalars(
        select(Release)
        .where(Release.organization_id == organization.id)
        .order_by(Release.kind, Release.name, Release.version)
    ).all()
    assert [(row.kind, row.name, row.version) for row in releases] == [
        (ReleaseKind.DOCKER, "seed-docker", "1.0.0-fail"),
        (ReleaseKind.DOCKER, "seed-docker", "1.0.0-ok"),
        (ReleaseKind.DOCKER_COMPOSE, "seed-compose", "1.0.0-fail"),
        (ReleaseKind.DOCKER_COMPOSE, "seed-compose", "1.0.0-ok"),
        (ReleaseKind.SYSTEMD, "seed-systemd", "1.0.0-fail"),
        (ReleaseKind.SYSTEMD, "seed-systemd", "1.0.0-ok"),
    ]
    assert {row.spec["unit_name"] for row in releases if row.kind == ReleaseKind.SYSTEMD} == {
        "seed-systemd.service"
    }
    assert {row.spec["container_name"] for row in releases if row.kind == ReleaseKind.DOCKER} == {
        "seed-nginx"
    }
    assert {row.spec["project_name"] for row in releases if row.kind == ReleaseKind.DOCKER_COMPOSE} == {
        "seed-compose"
    }
    assert sum(1 for row in releases if row.artifact_id is None) == 2  # docker image pulls

    artifacts = db_session.scalars(
        select(Artifact).where(Artifact.organization_id == organization.id)
    ).all()
    assert len(artifacts) == 4
    assert len(object_storage.objects) == 4


def test_seed_restores_missing_artifact_bytes(db_session, object_storage):
    seed(db_session, storage=object_storage)
    object_storage.objects.clear()
    seed(db_session, storage=object_storage)
    assert len(object_storage.objects) == 4


def test_seed_artifact_bytes_are_deterministic():
    first = [item.content for item in _demo_releases()]
    time.sleep(2.1)  # zip timestamps have 2-second resolution
    second = [item.content for item in _demo_releases()]
    assert first == second


def test_seed_repairs_artifact_checksum_drift(db_session, object_storage):
    seed(db_session, storage=object_storage)
    artifact = db_session.scalar(
        select(Artifact).where(Artifact.name == "seed-compose", Artifact.version == "1.0.0-ok")
    )
    assert artifact is not None
    # Simulate an older non-deterministic seed: stored bytes differ from the DB checksum.
    object_storage.objects[artifact.storage_key] = (b"stale zip bytes", "application/zip")
    artifact.checksum_sha256 = "0" * 64
    db_session.flush()

    seed(db_session, storage=object_storage)

    db_session.refresh(artifact)
    content, _ = object_storage.objects[artifact.storage_key]
    assert hashlib.sha256(content).hexdigest() == artifact.checksum_sha256
    assert artifact.size_bytes == len(content)


def test_seed_skips_file_artifacts_when_storage_is_offline(db_session, object_storage):
    object_storage.fail_puts = True
    seed(db_session, storage=object_storage)

    organization = db_session.scalar(select(Organization).where(Organization.slug == ORG_SLUG))
    assert organization is not None
    kinds = db_session.scalars(
        select(Release.kind).where(Release.organization_id == organization.id)
    ).all()
    assert set(kinds) == {ReleaseKind.DOCKER}
    assert db_session.scalar(select(Artifact).where(Artifact.organization_id == organization.id)) is None


def test_seed_leaves_a_user_artifact_with_a_seed_name_alone(db_session, object_storage):
    seed(db_session, storage=object_storage)
    artifact = db_session.scalar(
        select(Artifact).where(Artifact.name == "seed-compose", Artifact.version == "1.0.0-ok")
    )
    assert artifact is not None
    # Pretend a user uploaded it: no seed marker, their own bytes.
    artifact.metadata_ = {}
    object_storage.objects[artifact.storage_key] = (b"user bytes", "application/zip")
    artifact.checksum_sha256 = hashlib.sha256(b"user bytes").hexdigest()
    artifact.size_bytes = len(b"user bytes")
    db_session.flush()

    seed(db_session, storage=object_storage)

    db_session.refresh(artifact)
    assert object_storage.objects[artifact.storage_key][0] == b"user bytes"
    assert artifact.checksum_sha256 == hashlib.sha256(b"user bytes").hexdigest()


def test_seed_skips_the_release_when_the_artifact_repair_fails(db_session, object_storage):
    seed(db_session, storage=object_storage)
    artifact = db_session.scalar(
        select(Artifact).where(Artifact.name == "seed-compose", Artifact.version == "1.0.0-ok")
    )
    assert artifact is not None
    release = db_session.scalar(select(Release).where(Release.artifact_id == artifact.id))
    db_session.delete(release)
    object_storage.objects.clear()
    object_storage.fail_puts = True
    db_session.flush()

    seed(db_session, storage=object_storage)

    assert db_session.scalar(select(Release).where(Release.artifact_id == artifact.id)) is None
