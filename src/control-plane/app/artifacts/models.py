"""Artifact domain model.

An artifact is a stored, deployable file (OS image, firmware, compose bundle,
…). Only metadata lives in PostgreSQL; the content lives in object storage under
``storage_key``. Container images are out of scope: they belong to a future
OCI registry.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Enum,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.devices.models import DeviceType


class ArtifactType(enum.StrEnum):
    OS_IMAGE = "os_image"
    FIRMWARE = "firmware"
    DOCKER = "docker"
    DOCKER_COMPOSE = "docker_compose"
    SYSTEMD = "systemd"
    CONFIGURATION = "configuration"
    OTHER = "other"


class Artifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "artifacts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Device types this artifact is for (an OS image often fits several boards).
    # None: not hardware-specific.
    device_types: Mapped[list[DeviceType]] = relationship(
        secondary=lambda: artifact_device_types,
        lazy="selectin",
        order_by=lambda: DeviceType.name,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stored as VARCHAR (not a native PG enum) so new types need no ALTER TYPE.
    type: Mapped[ArtifactType] = mapped_column(
        Enum(
            ArtifactType,
            name="artifact_type",
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # OS images only: listed and downloadable without login (GET /api/v1/os-images), for Meteor Imager.
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())

    __table_args__ = (
        CheckConstraint("NOT published OR type = 'os_image'", name="ck_artifacts_published_os_image"),
        # Duplicates (same type, name and version on a shared device type) are refused in the
        # service; this index only makes that lookup fast.
        Index(
            "ix_artifacts_org_type_name_version",
            "organization_id",
            "type",
            func.lower(name),
            func.lower(version),
        ),
        UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
        Index("ix_artifacts_organization_id", "organization_id"),
        Index("ix_artifacts_org_type", "organization_id", "type"),
    )

    @property
    def device_type_ids(self) -> list[uuid.UUID]:
        return [device_type.id for device_type in self.device_types]

    @property
    def device_type_id(self) -> uuid.UUID | None:
        """The only device type, for clients from before artifacts could have several."""
        return self.device_types[0].id if len(self.device_types) == 1 else None


artifact_device_types = Table(
    "artifact_device_types",
    Base.metadata,
    Column("artifact_id", UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True),
    # RESTRICT: a device type that artifacts are built for cannot be deleted.
    Column("device_type_id", UUID(as_uuid=True), ForeignKey("device_types.id", ondelete="RESTRICT"), primary_key=True),
    Index("ix_artifact_device_types_device_type_id", "device_type_id"),
)
