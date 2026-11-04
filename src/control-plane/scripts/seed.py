"""Development seed data for identity and organizations."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any


# Host `make seed` must talk to Compose on localhost, not Docker DNS or AWS.
# Apply before importing Settings / SessionLocal (they cache DATABASE_URL).
def _apply_host_seed_env() -> None:
    env_file = Path(__file__).resolve().parents[3] / "deploy" / "compose" / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'\"")
    for key in ("OBJECT_STORAGE_ENDPOINT_URL", "DATABASE_URL", "REDIS_URL"):
        current = os.environ.get(key, "")
        rewritten = (
            current.replace("://minio:", "://127.0.0.1:")
            .replace("@postgres:", "@127.0.0.1:")
            .replace("://redis:", "://127.0.0.1:")
        )
        if rewritten != current:
            os.environ[key] = rewritten


if "pytest" not in sys.modules:
    _apply_host_seed_env()

# isort: off
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import object_storage as build_object_storage
from app.artifacts.models import Artifact, ArtifactType
from app.artifacts.repository import ArtifactRepository
from app.artifacts.schemas import ArtifactCreate
from app.artifacts.service import ArtifactService
from app.authorization.seed import seed_authorization_catalog
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import AppError, ConflictError
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.devices.models import Device, DeviceGroup, DeviceMqttCredential, DeviceType
from app.devices.tokens import hash_token
from app.identity.models import User
from app.identity.repository import UserRepository
from app.ports.storage import ObjectStorage
from app.releases.kinds import ReleaseKind
from app.releases.repository import ReleaseRepository
from app.releases.schemas import ReleaseCreate
from app.releases.service import ReleaseService
from app.tenancy.models import Organization, OrganizationRole
from app.tenancy.repository import MembershipRepository, OrganizationRepository
# isort: on

logger = logging.getLogger(__name__)

SEED_PASSWORD = "dev-password-123"

SEED_USERS = [
    {
        "email": "owner@example.com",
        "full_name": "Dev Owner",
        "is_superuser": True,
    },
    {
        "email": "admin@example.com",
        "full_name": "Dev Admin",
        "is_superuser": False,
    },
    {
        "email": "member@example.com",
        "full_name": "Dev Member",
        "is_superuser": False,
    },
    {
        "email": "viewer@example.com",
        "full_name": "Dev Viewer",
        "is_superuser": False,
    },
]

ORG_SLUG = "acme-energy"
ORG_NAME = "Acme Energy"
ORG_DESCRIPTION = "Development organization for Edge Platform"

SEED_DEVICE_TYPES = (
    {
        "name": "Raspberry Pi 5",
        "slug": "raspberry-pi-5",
        "description": "Development edge gateway",
        "manufacturer": "Raspberry Pi",
        "model": "5 Model B",
        "architecture": "arm64",
        "capabilities": {"gpio": True, "wifi": True, "mqtt": True},
    },
    {
        "name": "Intel NUC 13",
        "slug": "intel-nuc-13",
        "description": "Development x86 edge server",
        "manufacturer": "Intel",
        "model": "NUC13ANHi5",
        "architecture": "x86_64",
        "capabilities": {"containers": True, "wireguard": True, "mqtt": True},
    },
)

SEED_DEVICE_GROUPS = (
    {
        "name": "Storefronts",
        "slug": "storefronts",
        "description": "Customer-facing retail devices",
        "labels": {"environment": "production", "region": "us-east"},
    },
    {
        "name": "Lab",
        "slug": "lab",
        "description": "Development and validation devices",
        "labels": {"environment": "development"},
    },
)

# Same workload key per kind so good/fail Deploys replace each other on a device.
_SYSTEMD_UNIT = "seed-systemd.service"
_DOCKER_CONTAINER = "seed-nginx"
_COMPOSE_PROJECT = "seed-compose"

_SYSTEMD_OK_UNIT = b"""[Unit]
Description=MeteorCloud seed demo (ok)
[Service]
Type=simple
ExecStart=/bin/sleep infinity
Restart=no
[Install]
WantedBy=multi-user.target
"""

_SYSTEMD_FAIL_UNIT = b"""[Unit]
Description=MeteorCloud seed demo (fail)
[Service]
Type=oneshot
ExecStart=/bin/false
RemainAfterExit=no
[Install]
WantedBy=multi-user.target
"""

_COMPOSE_OK_YAML = """services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
"""

_COMPOSE_OK_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>MeteorCloud seed</title></head>
<body><h1>seed-compose 1.0.0-ok</h1></body>
</html>
"""

_COMPOSE_FAIL_YAML = """services:
  web:
    image: busybox:1.36
    command: ["false"]
    restart: "no"
"""


@dataclass(frozen=True)
class _SeedRelease:
    name: str
    version: str
    kind: ReleaseKind
    description: str
    spec: dict[str, Any]
    artifact_type: ArtifactType | None = None
    file_name: str | None = None
    content_type: str | None = None
    content: bytes | None = None


# Fixed entry timestamp so every seed run builds byte-identical zips (same SHA-256).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _compose_zip(compose_yaml: str, extra: dict[str, str] | None = None) -> bytes:
    buffer = BytesIO()
    entries = {"docker-compose.yml": compose_yaml, **(extra or {})}
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, body in entries.items():
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, body)
    return buffer.getvalue()


def _demo_releases() -> tuple[_SeedRelease, ...]:
    return (
        _SeedRelease(
            name="seed-systemd",
            version="1.0.0-ok",
            kind=ReleaseKind.SYSTEMD,
            description="Stays active. Use to test a successful systemd Deploy.",
            spec={"unit_name": _SYSTEMD_UNIT, "start": True},
            artifact_type=ArtifactType.SYSTEMD,
            file_name=_SYSTEMD_UNIT,
            content_type="text/plain",
            content=_SYSTEMD_OK_UNIT,
        ),
        _SeedRelease(
            name="seed-systemd",
            version="1.0.0-fail",
            kind=ReleaseKind.SYSTEMD,
            description="Exits immediately. Use to test a failing systemd Deploy.",
            spec={"unit_name": _SYSTEMD_UNIT, "start": True},
            artifact_type=ArtifactType.SYSTEMD,
            file_name=_SYSTEMD_UNIT,
            content_type="text/plain",
            content=_SYSTEMD_FAIL_UNIT,
        ),
        _SeedRelease(
            name="seed-docker",
            version="1.0.0-ok",
            kind=ReleaseKind.DOCKER,
            description="Pulls nginx:alpine. Use to test a successful Docker Deploy.",
            spec={
                "container_name": _DOCKER_CONTAINER,
                "image": "nginx:alpine",
                "ports": ["8080:80"],
                "restart": "unless-stopped",
            },
        ),
        _SeedRelease(
            name="seed-docker",
            version="1.0.0-fail",
            kind=ReleaseKind.DOCKER,
            description="busybox that exits. Use to test a failing Docker Deploy.",
            spec={
                "container_name": _DOCKER_CONTAINER,
                "image": "busybox:1.36",
                "command": "false",
                "restart": "no",
            },
        ),
        _SeedRelease(
            name="seed-compose",
            version="1.0.0-ok",
            kind=ReleaseKind.DOCKER_COMPOSE,
            description="Nginx + static HTML zip. Use to test a successful Compose Deploy.",
            spec={"project_name": _COMPOSE_PROJECT},
            artifact_type=ArtifactType.DOCKER_COMPOSE,
            file_name="seed-compose-ok.zip",
            content_type="application/zip",
            content=_compose_zip(_COMPOSE_OK_YAML, {"html/index.html": _COMPOSE_OK_HTML}),
        ),
        _SeedRelease(
            name="seed-compose",
            version="1.0.0-fail",
            kind=ReleaseKind.DOCKER_COMPOSE,
            description="Compose service that exits. Use to test a failing Compose Deploy.",
            spec={"project_name": _COMPOSE_PROJECT},
            artifact_type=ArtifactType.DOCKER_COMPOSE,
            file_name="seed-compose-fail.zip",
            content_type="application/zip",
            content=_compose_zip(_COMPOSE_FAIL_YAML),
        ),
    )


def _sync_seed_artifact_bytes(
    session: Session,
    storage: ObjectStorage,
    artifact: Artifact,
    *,
    content: bytes,
    content_type: str | None,
) -> bool:
    """Make storage and the DB row match the seed content for an existing artifact.

    Re-uploads when the object is missing or the stored checksum differs from the
    seed bytes (older seeds built non-deterministic zips, so devices got
    ``checksum_mismatch``), then updates ``checksum_sha256`` and ``size_bytes``.

    Returns False when the artifact cannot be used for a seed release: it was not created
    by the seed (a user upload with the same name and version is never overwritten), or
    its content could not be checked or restored. A later seed run retries the repair.
    """
    if not (artifact.metadata_ or {}).get("seeded"):
        logger.warning(
            "Artifact %s %s exists but was not created by the seed; leaving it unchanged",
            artifact.name,
            artifact.version,
        )
        return False
    expected = hashlib.sha256(content).hexdigest()
    try:
        present = storage.exists(artifact.storage_key)
    except Exception:
        logger.warning("Could not check storage for %s %s", artifact.name, artifact.version)
        return False
    if present and artifact.checksum_sha256 == expected and artifact.size_bytes == len(content):
        return True
    try:
        storage.put(artifact.storage_key, BytesIO(content), content_type=content_type)
    except Exception:
        logger.warning("Could not restore artifact content %s %s", artifact.name, artifact.version)
        return False
    artifact.checksum_sha256 = expected
    artifact.size_bytes = len(content)
    session.flush()
    logger.info(
        "%s artifact content %s %s",
        "Repaired" if present else "Restored missing",
        artifact.name,
        artifact.version,
    )
    return True


def _seed_demo_software(
    session: Session,
    *,
    actor: User,
    organization: Organization,
    storage: ObjectStorage | None,
) -> None:
    """Upload demo artifacts and create good/fail releases. Idempotent; storage is optional."""
    resolved = storage
    if resolved is None:
        try:
            resolved = build_object_storage(get_settings())
        except Exception:
            logger.warning(
                "Object storage is not available; seeding image-only docker releases only"
            )
            resolved = None

    artifact_svc = ArtifactService(session, resolved) if resolved is not None else None
    artifact_repo = ArtifactRepository(session)
    release_svc = ReleaseService(session)
    release_repo = ReleaseRepository(session)
    seeded: list[str] = []

    for item in _demo_releases():
        artifact_id = None
        if item.content is not None:
            if artifact_svc is None or item.artifact_type is None:
                logger.warning(
                    "Skipping %s %s: needs an artifact and object storage is unavailable",
                    item.name,
                    item.version,
                )
                continue
            existing_artifact = artifact_repo.find_duplicate(
                organization_id=organization.id,
                device_type_id=None,
                type=item.artifact_type,
                name=item.name,
                version=item.version,
            )
            if existing_artifact is None:
                try:
                    uploaded = artifact_svc.upload_artifact(
                        actor=actor,
                        organization_id=organization.id,
                        payload=ArtifactCreate(
                            name=item.name,
                            version=item.version,
                            type=item.artifact_type,
                            description=item.description,
                            metadata={"seeded": True},
                        ),
                        data=BytesIO(item.content),
                        file_name=item.file_name,
                        content_type=item.content_type,
                    )
                    artifact_id = uploaded.id
                    logger.info("Created artifact %s %s", item.name, item.version)
                except ConflictError:
                    existing_artifact = artifact_repo.find_duplicate(
                        organization_id=organization.id,
                        device_type_id=None,
                        type=item.artifact_type,
                        name=item.name,
                        version=item.version,
                    )
                    artifact_id = existing_artifact.id if existing_artifact else None
                    if existing_artifact is not None and not _sync_seed_artifact_bytes(
                        session,
                        resolved,
                        existing_artifact,
                        content=item.content,
                        content_type=item.content_type,
                    ):
                        logger.warning("Skipping release %s %s", item.name, item.version)
                        continue
                except AppError as exc:
                    if exc.status_code == 503 or exc.code in {"storage_unavailable", "artifact_empty"}:
                        logger.warning(
                            "Skipping remaining file artifacts (%s %s): %s",
                            item.name,
                            item.version,
                            exc.message,
                        )
                        artifact_svc = None
                        continue
                    raise
            else:
                artifact_id = existing_artifact.id
                if not _sync_seed_artifact_bytes(
                    session,
                    resolved,
                    existing_artifact,
                    content=item.content,
                    content_type=item.content_type,
                ):
                    logger.warning("Skipping release %s %s", item.name, item.version)
                    continue
                logger.info("Artifact already exists: %s %s", item.name, item.version)

        existing_release = release_repo.find_duplicate(
            organization_id=organization.id,
            kind=item.kind,
            name=item.name,
            version=item.version,
        )
        if existing_release is not None:
            logger.info("Release already exists: %s %s", item.name, item.version)
            seeded.append(f"{item.name} {item.version}")
            continue
        try:
            created = release_svc.create_release(
                actor=actor,
                organization_id=organization.id,
                payload=ReleaseCreate(
                    name=item.name,
                    version=item.version,
                    kind=item.kind,
                    artifact_id=artifact_id,
                    description=item.description,
                    spec=item.spec,
                ),
            )
            logger.info("Created release %s %s (%s)", created.name, created.version, created.kind.value)
            seeded.append(f"{created.name} {created.version}")
        except ConflictError:
            logger.info("Release already exists: %s %s", item.name, item.version)
            seeded.append(f"{item.name} {item.version}")

    if seeded:
        logger.info("Demo releases: %s", ", ".join(seeded))


def seed(session: Session, *, storage: ObjectStorage | None = None) -> None:
    seed_authorization_catalog(session)
    users = UserRepository(session)
    organizations = OrganizationRepository(session)
    memberships = MembershipRepository(session)

    created_users = {}
    for item in SEED_USERS:
        existing = users.get_by_email(item["email"])
        if existing is None:
            existing = users.create(
                email=item["email"],
                full_name=item["full_name"],
                password_hash=hash_password(SEED_PASSWORD),
                is_superuser=item["is_superuser"],
            )
            logger.info("Created user %s", item["email"])
        else:
            logger.info("User already exists: %s", item["email"])
        created_users[item["email"]] = existing

    organization = organizations.get_by_slug(ORG_SLUG)
    if organization is None:
        organization = organizations.create(
            name=ORG_NAME,
            slug=ORG_SLUG,
            description=ORG_DESCRIPTION,
            created_by_user_id=created_users["owner@example.com"].id,
        )
        logger.info("Created organization %s", ORG_SLUG)
    else:
        logger.info("Organization already exists: %s", ORG_SLUG)

    role_map = {
        "owner@example.com": OrganizationRole.OWNER,
        "admin@example.com": OrganizationRole.ADMIN,
        "member@example.com": OrganizationRole.OPERATOR,
        "viewer@example.com": OrganizationRole.VIEWER,
    }
    for email, role in role_map.items():
        user = created_users[email]
        existing_membership = memberships.get_user_membership(
            organization_id=organization.id,
            user_id=user.id,
        )
        if existing_membership is None:
            memberships.create(
                organization_id=organization.id,
                user_id=user.id,
                role=role,
            )
            logger.info("Added %s as %s", email, role.value)
        else:
            logger.info("Membership already exists for %s", email)

    device_types: dict[str, DeviceType] = {}
    for item in SEED_DEVICE_TYPES:
        device_type = session.scalar(
            select(DeviceType).where(
                DeviceType.organization_id == organization.id,
                DeviceType.slug == item["slug"],
            )
        )
        if device_type is None:
            device_type = DeviceType(
                organization_id=organization.id,
                name=item["name"],
                slug=item["slug"],
                description=item["description"],
                manufacturer=item["manufacturer"],
                model=item["model"],
                architecture=item["architecture"],
                capabilities=item["capabilities"],
                metadata_={"seeded": True},
            )
            session.add(device_type)
            session.flush()
            logger.info("Created device type %s", item["name"])
        device_types[item["slug"]] = device_type

    device_groups: dict[str, DeviceGroup] = {}
    for item in SEED_DEVICE_GROUPS:
        device_group = session.scalar(
            select(DeviceGroup).where(
                DeviceGroup.organization_id == organization.id,
                DeviceGroup.slug == item["slug"],
            )
        )
        if device_group is None:
            device_group = DeviceGroup(
                organization_id=organization.id,
                name=item["name"],
                slug=item["slug"],
                description=item["description"],
                labels=item["labels"],
                metadata_={"seeded": True},
            )
            session.add(device_group)
            session.flush()
            logger.info("Created device group %s", item["name"])
        device_groups[item["slug"]] = device_group

    now = datetime.now(UTC)
    seed_devices = (
        {
            "name": "store-gateway-01",
            "device_type": "raspberry-pi-5",
            "device_group": "storefronts",
            "serial_number": "MC-RPI5-001",
            "mac_addresses": ["02:42:ac:11:00:01"],
            "hostname": "store-gateway-01",
            "os_name": "Ubuntu",
            "os_version": "24.04 LTS",
            "kernel_version": "6.8.0-58-generic",
            "architecture": "arm64",
            "cpu_model": "Cortex-A76",
            "cpu_cores": 4,
            "memory_mb": 8192,
            "last_seen_at": now - timedelta(seconds=30),
            "mqtt_status": "online",
            "mqtt_metrics": {
                "cpu_percent": 18.4,
                "memory_percent": 42.1,
                "disk_percent": 35.7,
                "temperature_c": 49.2,
            },
        },
        {
            "name": "store-display-02",
            "device_type": "intel-nuc-13",
            "device_group": "storefronts",
            "serial_number": "MC-NUC13-002",
            "mac_addresses": ["02:42:ac:11:00:02"],
            "hostname": "store-display-02",
            "os_name": "Debian",
            "os_version": "12",
            "kernel_version": "6.1.0-28-amd64",
            "architecture": "x86_64",
            "cpu_model": "Intel Core i5-1340P",
            "cpu_cores": 12,
            "memory_mb": 16384,
            "last_seen_at": now - timedelta(hours=3),
            "mqtt_status": "offline",
            "mqtt_metrics": {
                "cpu_percent": 3.8,
                "memory_percent": 28.6,
                "disk_percent": 57.3,
                "temperature_c": 41.8,
            },
        },
        {
            "name": "lab-prototype-01",
            "device_type": "raspberry-pi-5",
            "device_group": "lab",
            "serial_number": "MC-LAB-001",
            "mac_addresses": ["02:42:ac:11:00:03"],
            "hostname": "lab-prototype-01",
            "os_name": "Ubuntu",
            "os_version": "24.04 LTS",
            "kernel_version": "6.8.0-58-generic",
            "architecture": "arm64",
            "cpu_model": "Cortex-A76",
            "cpu_cores": 4,
            "memory_mb": 4096,
            "last_seen_at": None,
            "mqtt_status": None,
            "mqtt_metrics": None,
        },
    )
    for item in seed_devices:
        device = session.scalar(
            select(Device).where(
                Device.organization_id == organization.id,
                Device.serial_number == item["serial_number"],
            )
        )
        if device is not None:
            logger.info("Device already exists: %s", item["name"])
            continue
        device = Device(
            organization_id=organization.id,
            name=item["name"],
            device_type_id=device_types[item["device_type"]].id,
            device_group_id=device_groups[item["device_group"]].id,
            serial_number=item["serial_number"],
            mac_addresses=item["mac_addresses"],
            hostname=item["hostname"],
            os_name=item["os_name"],
            os_version=item["os_version"],
            kernel_version=item["kernel_version"],
            architecture=item["architecture"],
            cpu_model=item["cpu_model"],
            cpu_cores=item["cpu_cores"],
            memory_mb=item["memory_mb"],
            labels={"seeded": "true"},
            metadata_={"seeded": True},
            credential_hash=hash_token(f"dev-seed-{item['serial_number']}"),
            credential_prefix="dev_seed",
            last_seen_at=item["last_seen_at"],
            registered_at=now,
            mqtt_status=item["mqtt_status"],
            mqtt_status_at=item["last_seen_at"],
            mqtt_metrics=item["mqtt_metrics"],
            mqtt_metrics_at=item["last_seen_at"],
        )
        session.add(device)
        session.flush()
        session.add(
            DeviceMqttCredential(
                device_id=device.id,
                password_hash=hash_token(f"mqtt-dev-seed-{item['serial_number']}"),
            )
        )
        logger.info("Created device %s", item["name"])

    _seed_demo_software(
        session,
        actor=created_users["owner@example.com"],
        organization=organization,
        storage=storage,
    )
    session.commit()


def main() -> int:
    settings = get_settings()
    if settings.app_env == "production":
        logger.error("Refusing to seed data when APP_ENV=production")
        return 1

    configure_logging(settings.log_level)
    session = SessionLocal()
    try:
        seed(session)
    except Exception:
        session.rollback()
        logger.exception("Seed failed")
        return 1
    finally:
        session.close()

    logger.info("Seed completed. Password for all seed users: %s", SEED_PASSWORD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
