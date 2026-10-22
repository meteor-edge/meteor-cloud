"""Per-device desired vs installed software (source of truth for sync)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.releases.kinds import ReleaseKind


class SoftwareSyncStatus(enum.StrEnum):
    IN_SYNC = "in_sync"
    PENDING = "pending"
    UPDATING = "updating"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SoftwareHealth(enum.StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class DeviceSoftware(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One workload on a device: desired release vs what the agent reported."""

    __tablename__ = "device_software"

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
    workload_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[ReleaseKind] = mapped_column(String(32), nullable=False)

    desired_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("releases.id", ondelete="SET NULL"),
        nullable=True,
    )
    desired_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    desired_spec: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    installed_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("releases.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    health: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SoftwareHealth.UNKNOWN.value,
        server_default=SoftwareHealth.UNKNOWN.value,
    )
    sync_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SoftwareSyncStatus.PENDING.value,
        server_default=SoftwareSyncStatus.PENDING.value,
    )
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("device_id", "workload_key", name="uq_device_software_device_workload"),
        Index("ix_device_software_organization_id", "organization_id"),
        Index("ix_device_software_device_id", "device_id"),
        Index("ix_device_software_desired_release_id", "desired_release_id"),
    )
