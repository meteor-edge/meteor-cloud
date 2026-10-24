"""Browser SSH session tickets."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ShellSessionStatus(enum.StrEnum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"


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
    ticket_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_device_shell_sessions_device_id", "device_id"),
        Index("ix_device_shell_sessions_organization_id", "organization_id"),
    )
