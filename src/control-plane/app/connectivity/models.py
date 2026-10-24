"""Per-device private-network (WireGuard) identity."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class ConnectivityStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class DeviceConnectivity(Base, TimestampMixin):
    __tablename__ = "device_connectivity"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="wireguard")
    wg_public_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    wg_address: Mapped[str | None] = mapped_column(INET, nullable=True, unique=True)
    # Login the agent installs the browser-SSH key for (pi, ubuntu, …). Empty: SHELL_SSH_USER.
    ssh_user: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ConnectivityStatus.PENDING.value,
        server_default=ConnectivityStatus.PENDING.value,
    )
    last_handshake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Device SSH host key ("<type> <base64>"), pinned on the first authenticated registration.
    ssh_host_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # "ca" once the agent trusts this server's SSH user CA (short-lived certificates); else the shared key.
    ssh_auth: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_ssh_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ssh_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_ssh_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_device_connectivity_organization_id", "organization_id"),
        Index("ix_device_connectivity_status", "status"),
    )
