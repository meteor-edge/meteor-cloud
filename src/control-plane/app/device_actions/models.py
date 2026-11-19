"""One-off actions ordered from the console: restart, shut down, SSH user changes.

The device collects an action once over its authenticated API and reports what
happened. Actions are never handed out twice, so a device that restarts does not
restart again. A password is held encrypted only until the device collects it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, LargeBinary, String, Text
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin

KIND_REBOOT = "reboot"
KIND_SHUTDOWN = "shutdown"
KIND_SET_PASSWORD = "set_password"
KIND_ADD_USER = "add_user"
KIND_UPDATE_AGENT = "update_agent"
POWER_KINDS = frozenset({KIND_REBOOT, KIND_SHUTDOWN})
USER_KINDS = frozenset({KIND_SET_PASSWORD, KIND_ADD_USER})
UPDATE_AGENT_KINDS = frozenset({KIND_UPDATE_AGENT})

STATE_PENDING = "pending"  # waiting for the device
STATE_SENT = "sent"  # collected by the device, no answer yet
STATE_DONE = "done"
STATE_FAILED = "failed"


class DeviceAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_actions"
    __table_args__ = (Index("ix_device_actions_device_id", "device_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=STATE_PENDING)
    # Version on the device when update_agent was ordered (X.Y.Z).
    from_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # User actions only.
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sudo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    authorized_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AES-GCM (app.core.secretbox). NULL once the device collected it, or the action expired.
    password_sealed: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Not collected by then: failed. A restart ordered for a device that was offline must not
    # happen hours later.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
