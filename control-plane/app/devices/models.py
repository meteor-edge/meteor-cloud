"""Fleet domain models: device types, groups, registration tokens, devices.

All fleet resources are strictly organization-scoped. PostgreSQL JSONB is used
only for free-form structures (capabilities, labels, MAC address arrays, and
custom metadata); every other attribute is an explicit column.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeviceType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named class of Linux device within an organization."""

    __tablename__ = "device_types"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_device_types_org_name"),
        Index("ix_device_types_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form list/dict of declared capabilities.
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )


class DeviceGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A logical grouping of devices within an organization."""

    __tablename__ = "device_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_device_groups_org_name"),
        Index("ix_device_groups_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )


class RegistrationToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A secret used by a device to self-register into an organization.

    Only the SHA-256 lookup hash is stored. The plaintext value is returned once
    at creation time and never persisted or logged.
    """

    __tablename__ = "registration_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_registration_tokens_token_hash"),
        Index("ix_registration_tokens_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Deterministic SHA-256 hash of the plaintext token, used for lookup.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Short non-secret prefix (e.g. "reg_ab12cd") for display/identification.
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    device_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    device_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_groups.id", ondelete="RESTRICT"),
        nullable=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A registered Linux device belonging to an organization."""

    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint(
            "credential_hash",
            name="uq_devices_credential_hash",
        ),
        Index("ix_devices_organization_id", "organization_id"),
        Index("ix_devices_org_serial_number", "organization_id", "serial_number"),
        Index("ix_devices_org_last_seen_at", "organization_id", "last_seen_at"),
        Index("ix_devices_org_device_type_id", "organization_id", "device_type_id"),
        Index("ix_devices_org_device_group_id", "organization_id", "device_group_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    device_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    device_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_groups.id", ondelete="RESTRICT"),
        nullable=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    # Hardware / OS identity used for duplicate detection.
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mac_addresses: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    # Inventory (all optional; agents tolerate missing values).
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kernel_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    architecture: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    # Free-form custom metadata. The DB column is named ``metadata`` but the
    # Python attribute is ``metadata_`` to avoid clashing with SQLAlchemy's
    # reserved ``Base.metadata``.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Per-device credential (only the SHA-256 hash is stored).
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    registration_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registration_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    mqtt_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mqtt_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    mqtt_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    mqtt_metrics_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class EnrollmentApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An organization-scoped API key used by the CLI to submit device-initiated
    enrollment requests.

    Only the SHA-256 lookup hash is stored. The plaintext value is returned once
    at creation time and never persisted or logged.
    """

    __tablename__ = "enrollment_api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_enrollment_api_keys_key_hash"),
        Index("ix_enrollment_api_keys_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )


class DeviceEnrollmentRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A device-initiated enrollment request awaiting admin review.

    On approval the device polls with its claim secret; the credential is issued
    (and the device row created) only at claim time. Only the SHA-256 hash of the
    claim secret is stored.
    """

    __tablename__ = "device_enrollment_requests"
    __table_args__ = (
        Index("ix_device_enrollment_requests_organization_id", "organization_id"),
        Index(
            "ix_device_enrollment_requests_org_status",
            "organization_id",
            "status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollment_api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    claim_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_secret_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    # Name requested by the device; the admin may override it at approval.
    requested_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Assigned by the admin at approval time.
    assigned_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    device_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_groups.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Captured inventory (mirrors Device; all optional).
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mac_addresses: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kernel_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    architecture: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class DeviceMqttCredential(Base, TimestampMixin):
    """Per-device MQTT username/password. Only the password hash is stored."""

    __tablename__ = "device_mqtt_credentials"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class DeviceCommand(Base, UUIDPrimaryKeyMixin):
    """Minimal ping command record. Not a generic command framework."""

    __tablename__ = "device_commands"
    __table_args__ = (
        Index("ix_device_commands_organization_id", "organization_id"),
        Index("ix_device_commands_device_id", "device_id"),
    )

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
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="ping")
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
