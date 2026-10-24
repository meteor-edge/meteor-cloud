"""Issue and consume browser SSH session tickets."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
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
        expired = list(
            self.session.scalars(
                select(DeviceShellSession).where(
                    DeviceShellSession.actor_user_id == actor.id,
                    DeviceShellSession.status == ShellSessionStatus.PENDING.value,
                    DeviceShellSession.ticket_expires_at <= now,
                )
            )
        )
        for pending in expired:
            pending.status = ShellSessionStatus.CLOSED.value
            pending.closed_at = now
        if expired:
            self.session.flush()
        open_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(DeviceShellSession)
                .where(
                    DeviceShellSession.actor_user_id == actor.id,
                    or_(
                        DeviceShellSession.status == ShellSessionStatus.OPEN.value,
                        and_(
                            DeviceShellSession.status == ShellSessionStatus.PENDING.value,
                            DeviceShellSession.ticket_expires_at > now,
                        ),
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
            ticket_expires_at=datetime.now(UTC)
            + timedelta(seconds=self.settings.shell_ticket_ttl_seconds),
        )
        self.session.add(row)
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
    ) -> tuple[DeviceShellSession, str, int, str, str | None]:
        """Validate a one-time ticket. Returns session, host, port, username, pinned host key."""
        self._require_provider()
        row = self.session.get(DeviceShellSession, session_id, with_for_update=True)
        if row is None:
            raise UnauthorizedError("invalid_shell_ticket", "Shell session was not found.")
        if row.status != ShellSessionStatus.PENDING.value:
            raise UnauthorizedError("invalid_shell_ticket", "This shell ticket was already used.")
        if datetime.now(UTC) > row.ticket_expires_at:
            row.status = ShellSessionStatus.CLOSED.value
            row.closed_at = datetime.now(UTC)
            self.session.commit()
            raise UnauthorizedError("invalid_shell_ticket", "This shell ticket has expired.")
        if not hmac.compare_digest(hash_token(ticket), row.ticket_hash):
            raise UnauthorizedError("invalid_shell_ticket", "Invalid shell ticket.")
        # The ticket is spent from here on, whatever happens next.
        row.status = ShellSessionStatus.CLOSED.value
        row.closed_at = datetime.now(UTC)
        self.session.commit()
        device = self.devices.get(organization_id=row.organization_id, device_id=row.device_id)
        if device is None or not device.is_enabled:
            raise ConflictError("device_disabled", "This device is not available.")
        # Access may have been removed after the ticket was issued.
        if row.actor_user_id is None:
            raise UnauthorizedError("invalid_shell_ticket", "The user who opened this session no longer exists.")
        membership = self.authz.get_active_membership(user_id=row.actor_user_id, organization_id=row.organization_id)
        if membership is None or not self.authz.check(
            membership, "device.shell", device_group_id=device.device_group_id
        ):
            raise ForbiddenError("insufficient_permission", "You no longer have shell access to this device.")
        peer = self.connectivity.require_reachable(device=device)
        host = self.connectivity.wg_host(peer)
        row.status = ShellSessionStatus.OPEN.value
        row.opened_at = datetime.now(UTC)
        row.closed_at = None
        self.session.commit()
        return row, host, self.settings.shell_ssh_port, self.connectivity.ssh_user_for(peer), peer.ssh_host_key

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
