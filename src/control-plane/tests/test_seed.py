from sqlalchemy import select

from app.artifacts.models import Artifact
from app.devices.models import Device, DeviceGroup, DeviceType
from app.releases.kinds import ReleaseKind
from app.releases.models import Release
from app.tenancy.models import Organization
from scripts.seed import ORG_SLUG, seed


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
