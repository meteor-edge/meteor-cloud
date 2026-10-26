"""Public application endpoint registry."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EndpointAvailability(enum.StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


class ApplicationEndpoint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "application_endpoints"

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
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    device_software_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_software.id", ondelete="SET NULL"),
        nullable=True,
    )
    workload_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="http")
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    availability: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EndpointAvailability.UNKNOWN.value,
        server_default=EndpointAvailability.UNKNOWN.value,
    )

    __table_args__ = (
        CheckConstraint("port >= 1 AND port <= 65535", name="ck_application_endpoints_port"),
        Index("ix_application_endpoints_organization_id", "organization_id"),
        Index("ix_application_endpoints_device_id", "device_id"),
        Index(
            "uq_application_endpoints_device_port_enabled",
            "device_id",
            "port",
            unique=True,
            postgresql_where=text("enabled IS TRUE"),
        ),
    )
