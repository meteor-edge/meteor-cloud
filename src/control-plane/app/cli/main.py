"""Backend management commands."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.identity.repository import UserRepository

logger = logging.getLogger(__name__)


def create_admin() -> int:
    email = os.environ.get("EDGE_PLATFORM_ADMIN_EMAIL", "").strip()
    password = os.environ.get("EDGE_PLATFORM_ADMIN_PASSWORD", "").strip()
    if not email or not password:
        logger.info("Admin credentials not configured; skipping create-admin.")
        return 0

    session = SessionLocal()
    try:
        users = UserRepository(session)
        existing = users.get_by_email(email)
        if existing is not None:
            logger.info("Administrator account already exists for %s", email)
            return 0

        users.create(
            email=email,
            full_name="Platform Administrator",
            password_hash=hash_password(password),
            is_superuser=True,
        )
        session.commit()
        logger.info("Created administrator account for %s", email)
        return 0
    except Exception:
        session.rollback()
        logger.exception("Failed to create administrator account")
        return 1
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-admin", help="Create the initial administrator if missing.")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin()
    parser.error(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
