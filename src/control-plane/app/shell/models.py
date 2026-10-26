"""Browser SSH and device log session tickets."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ShellSessionStatus(enum.StrEnum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"


class SessionKind(enum.StrEnum):
    SHELL = "shell"
    LOGS = "logs"


class DeviceShellSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_shell_sessions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ticket_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ShellSessionStatus.PENDING.value,
        server_default=ShellSessionStatus.PENDING.value,
    )
    # shell: interactive PTY (device.shell); logs: one read-only log command (device.logs).
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SessionKind.SHELL.value,
        server_default=SessionKind.SHELL.value,
    )
    # Log sessions: the validated source and options the command is built from.
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ticket_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_device_shell_sessions_device_id", "device_id"),
        Index("ix_device_shell_sessions_organization_id", "organization_id"),
    )
