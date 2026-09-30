"""Development seed data for identity and organizations."""

from __future__ import annotations

import logging
import sys

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.security import hash_password
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


def seed(session: Session) -> None:
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
        "member@example.com": OrganizationRole.MEMBER,
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
