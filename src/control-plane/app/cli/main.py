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


def shell_target(target: str) -> int:
    """Print "<user>\t<host>\t<port>\t<host key or ->" for meteorcloud-ssh, from trusted device records."""
    import ipaddress
    import uuid

    from sqlalchemy import cast, select
    from sqlalchemy.dialects.postgresql import INET

    from app.connectivity.models import DeviceConnectivity
    from app.connectivity.service import ConnectivityService
    from app.core.exceptions import AppError
    from app.devices.models import Device

    settings = get_settings()
    session = SessionLocal()
    try:
        row = None
        try:
            row = session.get(DeviceConnectivity, uuid.UUID(target))
        except ValueError:
            try:
                address = str(ipaddress.ip_address(target))
            except ValueError:
                print(f"meteorcloud-ssh: {target!r} is not a device ID or WireGuard address.", file=sys.stderr)
                return 2
            row = session.scalar(select(DeviceConnectivity).where(DeviceConnectivity.wg_address == cast(address, INET)))
        device = session.get(Device, row.device_id) if row is not None else None
        if row is None or device is None:
            print(f"meteorcloud-ssh: no device is registered at {target}.", file=sys.stderr)
            return 3
        service = ConnectivityService(session, provider=None, settings=settings)
        try:
            service.require_reachable(device=device)
        except AppError as exc:
            state = service.state_for(row, device)
            print(f"meteorcloud-ssh: {device.name} ({device.id}) is {state}: {exc.message}", file=sys.stderr)
            return 3
        print(
            f"{service.ssh_user_for(row)}\t{service.wg_host(row)}\t{settings.shell_ssh_port}\t{row.ssh_host_key or '-'}"
        )
        return 0
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-admin", help="Create the initial administrator if missing.")
    target_parser = subparsers.add_parser("shell-target", help="Resolve a device for meteorcloud-ssh.")
    target_parser.add_argument("target", help="Device ID or WireGuard address")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin()
    if args.command == "shell-target":
        return shell_target(args.target)
    parser.error(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
