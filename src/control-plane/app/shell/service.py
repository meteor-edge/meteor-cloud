"""Issue and consume browser SSH session tickets."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.connectivity.service import ConnectivityService
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.devices.repository import DeviceRepository
from app.devices.tokens import generate_shell_ticket, hash_token
from app.identity.models import User
from app.ports.shell import DeviceShellProvider
from app.shell.models import DeviceShellSession, ShellSessionStatus
from app.shell.schemas import ShellSessionCreateResponse
from app.tenancy.models import OrganizationMembership


def _new_ticket() -> tuple[str, str]:
    token = generate_shell_ticket()
    return token.plaintext, token.token_hash


def close_open_sessions_for_device(session: Session, *, device_id: uuid.UUID) -> int:
    """Mark pending/open shells for a device closed. Live bridges notice on their next recheck.

    Does not commit; the caller owns the transaction.
    """
    result = session.execute(
        update(DeviceShellSession)
        .where(
            DeviceShellSession.device_id == device_id,
            DeviceShellSession.status.in_((ShellSessionStatus.PENDING.value, ShellSessionStatus.OPEN.value)),
        )
        .values(status=ShellSessionStatus.CLOSED.value, closed_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


class ShellService:
    def __init__(
        self,
        session: Session,
        *,
        connectivity: ConnectivityService,
        provider: DeviceShellProvider | None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.connectivity = connectivity
        self.provider = provider
        self.settings = settings or get_settings()
        self.devices = DeviceRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def _require_membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        return self.authz.require_membership(user_id=user_id, organization_id=organization_id)

    def _require_provider(self) -> DeviceShellProvider:
        if self.provider is None:
            raise NotFoundError("shell_disabled", "Browser SSH is disabled for this deployment.")
        return self.provider

    def create_session(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> ShellSessionCreateResponse:
        membership = self._require_membership(organization_id, actor.id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        if not device.is_enabled:
            raise ConflictError("device_disabled", "This device is disabled.")
        try:
            self.authz.require(membership, "device.shell", device_group_id=device.device_group_id)
        except ForbiddenError:
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action="device.shell.denied",
                resource_type="device",
                resource_id=device_id,
                metadata={"reason": "permission"},
            )
            raise
        self._require_provider()
        peer = self.connectivity.require_reachable(device=device)
        now = datetime.now(UTC)
        open_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(DeviceShellSession)
                .where(
                    DeviceShellSession.actor_user_id == actor.id,
                    # A pending ticket past its TTL can never be used, so it does not count.
                    (DeviceShellSession.status == ShellSessionStatus.OPEN.value)
                    | (
                        (DeviceShellSession.status == ShellSessionStatus.PENDING.value)
                        & (DeviceShellSession.ticket_expires_at > now)
                    ),
                )
            )
            or 0
        )
        if open_count >= self.settings.shell_max_sessions_per_user:
            raise ConflictError("shell_session_limit", "Too many open shell sessions.")
        plaintext, ticket_hash = _new_ticket()
        row = DeviceShellSession(
            organization_id=organization_id,
            device_id=device_id,
            actor_user_id=actor.id,
            ticket_hash=ticket_hash,
            status=ShellSessionStatus.PENDING.value,
            ticket_expires_at=now + timedelta(seconds=self.settings.shell_ticket_ttl_seconds),
        )
        self.session.add(row)
        # Assign the primary key before it goes into the audit record.
        self.session.flush()
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="device.shell.start",
            resource_type="device",
            resource_id=device_id,
            metadata={"session_id": str(row.id), "wg_address": str(peer.wg_address)},
        )
        self.session.commit()
        self.session.refresh(row)
        return ShellSessionCreateResponse(
            session_id=row.id,
            ticket=plaintext,
            websocket_path=f"/api/v1/shell-sessions/{row.id}/ws",
            expires_at=row.ticket_expires_at,
            device_id=device_id,
        )

    def consume_ticket(
        self, *, session_id: uuid.UUID, ticket: str
    ) -> tuple[DeviceShellSession, str, int, str]:
        """Validate a one-time ticket. Returns session, host, port, username."""
        self._require_provider()
        row = self.session.get(DeviceShellSession, session_id)
        if row is None:
            raise UnauthorizedError("invalid_shell_ticket", "Shell session was not found.")
        if row.status != ShellSessionStatus.PENDING.value:
            raise UnauthorizedError("invalid_shell_ticket", "This shell ticket was already used.")
        now = datetime.now(UTC)
        if now > row.ticket_expires_at:
            row.status = ShellSessionStatus.CLOSED.value
            row.closed_at = now
            self.session.commit()
            raise UnauthorizedError("invalid_shell_ticket", "This shell ticket has expired.")
        if not hmac.compare_digest(hash_token(ticket), row.ticket_hash):
            raise UnauthorizedError("invalid_shell_ticket", "Invalid shell ticket.")
        device = self.devices.get(organization_id=row.organization_id, device_id=row.device_id)
        if device is None or not device.is_enabled:
            raise ConflictError("device_disabled", "This device is not available.")
        peer = self.connectivity.require_reachable(device=device)
        host = self.connectivity.wg_host(peer)
        # Claim the ticket atomically so two concurrent connects cannot both open it.
        claimed = self.session.execute(
            update(DeviceShellSession)
            .where(
                DeviceShellSession.id == session_id,
                DeviceShellSession.status == ShellSessionStatus.PENDING.value,
            )
            .values(status=ShellSessionStatus.OPEN.value, opened_at=now)
            .execution_options(synchronize_session=False)
        )
        if not claimed.rowcount:
            self.session.rollback()
            raise UnauthorizedError("invalid_shell_ticket", "This shell ticket was already used.")
        self.session.commit()
        self.session.refresh(row)
        return row, host, self.settings.shell_ssh_port, self.settings.shell_ssh_user

    def session_still_allowed(self, session_id: uuid.UUID) -> bool:
        """True while the shell is open, its device is enabled, and connectivity is active."""
        try:
            row = self.session.get(DeviceShellSession, session_id, populate_existing=True)
            if row is None or row.status != ShellSessionStatus.OPEN.value:
                return False
            device = self.devices.get(organization_id=row.organization_id, device_id=row.device_id)
            if device is None or not device.is_enabled:
                return False
            try:
                self.connectivity.require_reachable(device=device)
            except ConflictError:
                return False
            return True
        finally:
            # End the read transaction so the pooled connection is not held between checks.
            self.session.rollback()

    def close_session(self, session_id: uuid.UUID, *, actor: User | None = None) -> None:
        row = self.session.get(DeviceShellSession, session_id)
        if row is None:
            return
        if row.status == ShellSessionStatus.CLOSED.value:
            return
        row.status = ShellSessionStatus.CLOSED.value
        row.closed_at = datetime.now(UTC)
        self.audit.record(
            actor=actor,
            organization_id=row.organization_id,
            action="device.shell.end",
            resource_type="device",
            resource_id=row.device_id,
            metadata={"session_id": str(row.id)},
        )
        self.session.commit()
