"""Deployment campaign history (a Deploy click) and per-device progress."""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeploymentStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DeploymentDeviceStatus(enum.StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    UPDATING = "updating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class Deployment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deployments"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    rollout: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default='{"strategy": "immediate"}',
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DeploymentStatus.IN_PROGRESS.value,
        server_default=DeploymentStatus.IN_PROGRESS.value,
    )
    # Which execution engine ran this Deploy (native, mender, …).
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="native",
        server_default="native",
    )
    # Vendor-side id when provider is external (e.g. Mender deployment id).
    provider_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rollback_of_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deployments.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_deployments_organization_id", "organization_id"),
        Index("ix_deployments_org_status", "organization_id", "status"),
        Index("ix_deployments_release_id", "release_id"),
        Index("ix_deployments_provider_ref", "provider", "provider_ref"),
    )


class DeploymentDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deployment_devices"

    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deployments.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DeploymentDeviceStatus.PENDING.value,
        server_default=DeploymentDeviceStatus.PENDING.value,
    )
    previous_desired_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("releases.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("deployment_id", "device_id", name="uq_deployment_devices_deployment_device"),
        Index("ix_deployment_devices_deployment_id", "deployment_id"),
        Index("ix_deployment_devices_device_id", "device_id"),
    )
