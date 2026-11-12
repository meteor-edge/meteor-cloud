"""Wi-Fi networks a device should know, managed from the console.

A row is the desired state for one network on one device. The password is held
encrypted only until the device reports what happened to the change, then erased.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin

# What the device should do with the row.
ACTION_APPLY = "apply"
ACTION_REMOVE = "remove"

# Where the change stands.
STATE_PENDING = "pending"  # waiting for the device
STATE_APPLIED = "applied"
STATE_ROLLED_BACK = "rolled_back"  # the device tried it, lost the server, and went back
STATE_FAILED = "failed"

SECURITY_WPA_PSK = "wpa-psk"  # WPA2/WPA3 personal: one password
SECURITY_OPEN = "open"


class DeviceWifiNetwork(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_wifi_networks"
    __table_args__ = (
        UniqueConstraint("device_id", "ssid", name="uq_device_wifi_networks_device_ssid"),
        Index("ix_device_wifi_networks_device_id", "device_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    ssid: Mapped[str] = mapped_column(String(64), nullable=False)
    security: Mapped[str] = mapped_column(String(16), nullable=False, default=SECURITY_WPA_PSK)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    # True: connect now and go back if the server cannot be reached. False: save for the next connection.
    connect_now: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())

    action: Mapped[str] = mapped_column(String(16), nullable=False, default=ACTION_APPLY)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=STATE_PENDING)
    # Bumped on every change, so a device's late report about an older change is ignored.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # AES-GCM (app.core.secretbox). NULL once the device reported, or when none is needed.
    password_sealed: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    password_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DeviceWifiStatus(Base, TimestampMixin):
    """What the device last said about its Wi-Fi (never a password)."""

    __tablename__ = "device_wifi_status"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    # Why Wi-Fi cannot be managed (no NetworkManager, no radio, helper not installed).
    unsupported_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_ssid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signal_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interface: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # True when another link (Ethernet) also reaches the network, so Wi-Fi changes cannot cut the device off.
    wired_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    # Networks saved on the device: [{"ssid": ..., "managed": bool}].
    saved_networks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # The link traffic leaves on (default route): ethernet, wifi, cellular or other.
    uplink_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    uplink_interface: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
