"""Backend management commands."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.identity.repository import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.connectivity.models import DeviceConnectivity
    from app.connectivity.service import ConnectivityService
    from app.core.config import Settings
    from app.devices.models import Device

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


DEMO_ORG_NAME = "Demo Organization"
DEMO_ORG_SLUG = "demo-organization"


def create_demo() -> int:
    """Create Demo Organization and make the admin its owner. Idempotent."""
    email = os.environ.get("EDGE_PLATFORM_ADMIN_EMAIL", "").strip()
    if not email:
        logger.info("Admin email not configured; skipping create-demo.")
        return 0

    from app.tenancy.models import OrganizationRole
    from app.tenancy.repository import MembershipRepository, OrganizationRepository

    session = SessionLocal()
    try:
        user = UserRepository(session).get_by_email(email)
        if user is None:
            logger.error("create-demo needs the administrator account (%s) first.", email)
            return 1
        organizations = OrganizationRepository(session)
        memberships = MembershipRepository(session)
        organization = organizations.get_by_slug(DEMO_ORG_SLUG)
        if organization is None:
            organization = organizations.create(
                name=DEMO_ORG_NAME,
                slug=DEMO_ORG_SLUG,
                description="Seeded so you can explore the console after make up.",
                created_by_user_id=user.id,
            )
            logger.info("Created organization %s", DEMO_ORG_SLUG)
        else:
            logger.info("Organization already exists: %s", DEMO_ORG_SLUG)
        existing = memberships.get_user_membership(
            organization_id=organization.id,
            user_id=user.id,
        )
        if existing is None:
            memberships.create(
                organization_id=organization.id,
                user_id=user.id,
                role=OrganizationRole.OWNER,
            )
            logger.info("Added %s as owner of %s", email, DEMO_ORG_SLUG)
        session.commit()
        return 0
    except Exception:
        session.rollback()
        logger.exception("Failed to create demo organization")
        return 1
    finally:
        session.close()


def _resolve_device(
    session: Session, target: str, settings: Settings
) -> tuple[DeviceConnectivity, Device, ConnectivityService] | int:
    """(connectivity row, device, service) for a reachable device, or an exit code (message on stderr)."""
    import ipaddress
    import uuid

    from sqlalchemy import cast, select
    from sqlalchemy.dialects.postgresql import INET

    from app.connectivity.models import DeviceConnectivity
    from app.connectivity.service import ConnectivityService
    from app.core.exceptions import AppError
    from app.devices.models import Device

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
    return row, device, service


def shell_target(target: str) -> int:
    """Print "<user>\t<host>\t<port>\t<host key or ->\t<ca|key>" for meteorcloud-ssh, from device records."""
    settings = get_settings()
    session = SessionLocal()
    try:
        resolved = _resolve_device(session, target, settings)
        if isinstance(resolved, int):
            return resolved
        row, _device, service = resolved
        auth = "ca" if service.uses_certificates(row) else "key"
        print(
            f"{service.ssh_user_for(row)}\t{service.wg_host(row)}\t{settings.shell_ssh_port}"
            f"\t{row.ssh_host_key or '-'}\t{auth}"
        )
        return 0
    finally:
        session.close()


def sign_ssh_cert(target: str, identity: str, public_key: str) -> int:
    """Print a short-lived certificate for ``public_key`` that opens this one device (meteorcloud-ssh)."""
    import tempfile
    from pathlib import Path

    from app.adapters import ssh_certificates
    from app.audit.service import AuditRecorder
    from app.connectivity.schemas import normalize_ssh_host_key
    from app.connectivity.service import ssh_principal
    from app.core.exceptions import AppError

    settings = get_settings()
    try:
        key = normalize_ssh_host_key(public_key)
    except ValueError as exc:
        print(f"meteorcloud-ssh: {exc}", file=sys.stderr)
        return 2
    session = SessionLocal()
    try:
        resolved = _resolve_device(session, target, settings)
        if isinstance(resolved, int):
            return resolved
        row, device, service = resolved
        if not service.uses_certificates(row):
            print(f"meteorcloud-ssh: {device.name} does not trust the SSH certificate authority yet.", file=sys.stderr)
            return 4
        key_id = f"cli:{identity}"
        with tempfile.TemporaryDirectory(prefix="meteorcloud-sign-") as directory:
            pub = Path(directory) / "operator.pub"
            pub.write_text(key + "\n", encoding="utf-8")
            try:
                cert = ssh_certificates.sign_public_key(
                    pub,
                    ca_file=settings.shell_ssh_ca_file,
                    key_id=key_id,
                    principal=ssh_principal(device.id),
                    ttl_seconds=settings.shell_ssh_cert_ttl_seconds,
                )
            except AppError as exc:
                print(f"meteorcloud-ssh: {exc.message}", file=sys.stderr)
                return 1
            certificate = cert.read_text(encoding="utf-8")
        AuditRecorder(session).record(
            actor=None,
            organization_id=device.organization_id,
            action="device.shell.certificate",
            resource_type="device",
            resource_id=device.id,
            metadata={"key_id": ssh_certificates.safe_key_id(key_id), "via": "meteorcloud-ssh"},
        )
        session.commit()
        print(certificate, end="")
        return 0
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-admin", help="Create the initial administrator if missing.")
    subparsers.add_parser("create-demo", help="Create Demo Organization for the administrator.")
    target_parser = subparsers.add_parser("shell-target", help="Resolve a device for meteorcloud-ssh.")
    target_parser.add_argument("target", help="Device ID or WireGuard address")
    sign_parser = subparsers.add_parser(
        "sign-ssh-cert", help="Sign a public key (stdin) for one device; prints the certificate."
    )
    sign_parser.add_argument("target", help="Device ID or WireGuard address")
    sign_parser.add_argument("--identity", required=True, help="Who asked, for the audit log (user@host)")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin()
    if args.command == "create-demo":
        return create_demo()
    if args.command == "shell-target":
        return shell_target(args.target)
    if args.command == "sign-ssh-cert":
        return sign_ssh_cert(args.target, args.identity, sys.stdin.read(4096))
    parser.error(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
