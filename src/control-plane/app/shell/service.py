"""Issue and consume browser SSH and device log session tickets."""

from __future__ import annotations

import hmac
import shlex
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.connectivity.models import ConnectivityStatus
from app.connectivity.service import ConnectivityService, ssh_principal
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.deployments.software_repository import DeviceSoftwareRepository
from app.devices.models import Device
from app.devices.repository import DeviceRepository
from app.devices.tokens import generate_shell_ticket, hash_token
from app.identity.models import User
from app.ports.shell import DeviceShellProvider, ShellCertificate
from app.shell.logs import (
    LogSessionCreateRequest,
    LogSource,
    LogSourcesResponse,
    deployed_keys,
    log_command,
    source_key,
)
from app.shell.models import DeviceShellSession, SessionKind, ShellSessionStatus
from app.shell.schemas import ShellSessionCreateResponse
from app.tenancy.models import OrganizationMembership

# The permission that opens (and keeps open) each kind of session.
_PERMISSION = {SessionKind.SHELL.value: "device.shell", SessionKind.LOGS.value: "device.logs"}


def _new_ticket() -> tuple[str, str]:
    token = generate_shell_ticket()
    return token.plaintext, token.token_hash


def close_sessions_for_device(session: Session, *, device_id: uuid.UUID) -> int:
    """Mark a device's pending and open shells closed (no commit). Open bridges end on their next recheck."""
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


@dataclass(frozen=True)
class ShellTarget:
    """Where and how to open a redeemed session; never taken from the client."""

    session: DeviceShellSession
    host: str
    port: int
    username: str
    host_key: str | None
    certificate: ShellCertificate | None
    # Log sessions: the read-only command to run instead of an interactive shell.
    command: list[str] | None = None


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

    def _authorized_device(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID, kind: str) -> Device:
        membership = self._require_membership(organization_id, actor.id)
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        try:
            self.authz.require(membership, _PERMISSION[kind], device_group_id=device.device_group_id)
        except ForbiddenError:
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action=f"{_PERMISSION[kind]}.denied",
                resource_type="device",
                resource_id=device_id,
                metadata={"reason": "permission"},
            )
            raise
        return device

    def create_session(
        self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> ShellSessionCreateResponse:
        return self._create(
            actor=actor, organization_id=organization_id, device_id=device_id, kind=SessionKind.SHELL.value
        )

    def log_sources(self, *, actor: User, organization_id: uuid.UUID, device_id: uuid.UUID) -> LogSourcesResponse:
        device = self._authorized_device(
            actor=actor, organization_id=organization_id, device_id=device_id, kind=SessionKind.LOGS.value
        )
        workloads = DeviceSoftwareRepository(self.session).list_for_device(device_id=device.id)
        return LogSourcesResponse(
            sources=[LogSource.model_validate(item) for item in device.log_sources or []],
            reported_at=device.log_sources_at,
            deployed=deployed_keys(workloads),
        )

    def create_log_session(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        request: LogSessionCreateRequest,
    ) -> ShellSessionCreateResponse:
        return self._create(
            actor=actor,
            organization_id=organization_id,
            device_id=device_id,
            kind=SessionKind.LOGS.value,
            log_request=request,
        )

    def _create(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        kind: str,
        log_request: LogSessionCreateRequest | None = None,
    ) -> ShellSessionCreateResponse:
        device = self._authorized_device(actor=actor, organization_id=organization_id, device_id=device_id, kind=kind)
        if not device.is_enabled:
            raise ConflictError("device_disabled", "This device is disabled.")
        params = None
        if log_request is not None:
            # Only a source the agent listed: the device would refuse anything else anyway.
            reported = {source_key(item["kind"], item["name"]) for item in device.log_sources or []}
            if source_key(log_request.kind, log_request.name) not in reported:
                raise NotFoundError("log_source_not_found", "This device has not reported that log source.")
            params = log_request.model_dump(mode="json")
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
            raise ConflictError("shell_session_limit", "Too many open shell and log sessions.")
        plaintext, ticket_hash = _new_ticket()
        row = DeviceShellSession(
            organization_id=organization_id,
            device_id=device_id,
            actor_user_id=actor.id,
            ticket_hash=ticket_hash,
            status=ShellSessionStatus.PENDING.value,
            kind=kind,
            params=params,
            ticket_expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.shell_ticket_ttl_seconds),
        )
        self.session.add(row)
        # The id is assigned at flush; the audit record needs it.
        self.session.flush()
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action=f"{_PERMISSION[kind]}.start",
            resource_type="device",
            resource_id=device_id,
            metadata={
                "session_id": str(row.id),
                "wg_address": str(peer.wg_address),
                **({"log": params} if params else {}),
            },
        )
        self.session.commit()
        self.session.refresh(row)
        return ShellSessionCreateResponse(
            session_id=row.id,
            ticket=plaintext,
            websocket_path=f"/api/v1/{'log' if kind == SessionKind.LOGS.value else 'shell'}-sessions/{row.id}/ws",
            expires_at=row.ticket_expires_at,
            device_id=device_id,
        )

    def consume_ticket(self, *, session_id: uuid.UUID, ticket: str, kind: str = SessionKind.SHELL.value) -> ShellTarget:
        """Validate a one-time ticket and return the device to open.

        ``kind`` is the websocket's: a log ticket never opens a shell, nor the reverse.
        """
        self._require_provider()
        row = self.session.get(DeviceShellSession, session_id, with_for_update=True)
        if row is None or row.kind != kind:
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
            membership, _PERMISSION[row.kind], device_group_id=device.device_group_id
        ):
            what = "log" if row.kind == SessionKind.LOGS.value else "shell"
            raise ForbiddenError("insufficient_permission", f"You no longer have {what} access to this device.")
        command = log_command(row.params or {}) if row.kind == SessionKind.LOGS.value else None
        peer = self.connectivity.require_reachable(device=device)
        host = self.connectivity.wg_host(peer)
        certificate = None
        if self.connectivity.uses_certificates(peer):
            actor = self.session.get(User, row.actor_user_id)
            certificate = ShellCertificate(
                principal=ssh_principal(device.id),
                key_id=f"{row.kind}:{row.id}:{actor.email if actor else row.actor_user_id}",
                # The certificate itself only runs this command: it cannot open a shell.
                force_command=shlex.join(command) if command else None,
            )
        elif self.settings.shell_gateway_key_retired:
            raise ConflictError(
                "shell_certificate_required",
                "This device does not trust the SSH certificate authority yet. Update its agent, then retry.",
            )
        row.status = ShellSessionStatus.OPEN.value
        row.opened_at = datetime.now(UTC)
        row.closed_at = None
        self.session.commit()
        return ShellTarget(
            session=row,
            host=host,
            port=self.settings.shell_ssh_port,
            username=self.connectivity.ssh_user_for(peer),
            host_key=peer.ssh_host_key,
            certificate=certificate,
            command=command,
        )

    def session_still_allowed(self, session_id: uuid.UUID) -> bool:
        """An open shell may continue: not closed (revoke), device usable, peer active, user still allowed."""
        try:
            row = self.session.get(DeviceShellSession, session_id, populate_existing=True)
            if row is None or row.status != ShellSessionStatus.OPEN.value or row.actor_user_id is None:
                return False
            device = self.devices.get(organization_id=row.organization_id, device_id=row.device_id)
            if device is None or not device.is_enabled or device.credential_hash is None:
                return False
            peer = self.connectivity.rows.get(device.id)
            if peer is None or peer.status != ConnectivityStatus.ACTIVE.value or peer.wg_address is None:
                return False
            membership = self.authz.get_active_membership(
                user_id=row.actor_user_id, organization_id=row.organization_id
            )
            return membership is not None and self.authz.check(
                membership, _PERMISSION[row.kind], device_group_id=device.device_group_id
            )
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
            action=f"{_PERMISSION[row.kind]}.end",
            resource_type="device",
            resource_id=row.device_id,
            metadata={"session_id": str(row.id)},
        )
        self.session.commit()
