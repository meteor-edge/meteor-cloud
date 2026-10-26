"""Development seed data for identity and organizations."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.seed import seed_authorization_catalog
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.devices.models import Device, DeviceGroup, DeviceMqttCredential, DeviceType
from app.devices.tokens import hash_token
from app.identity.repository import UserRepository
from app.tenancy.models import OrganizationRole
from app.tenancy.repository import MembershipRepository, OrganizationRepository

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


def seed(session: Session) -> None:
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
