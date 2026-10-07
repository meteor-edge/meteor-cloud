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

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ArtifactType(enum.StrEnum):
    OS_IMAGE = "os_image"
    FIRMWARE = "firmware"
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
    # Optional: some artifacts (e.g. configuration) are not hardware-specific.
    device_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_types.id", ondelete="RESTRICT"),
        nullable=True,
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

    __table_args__ = (
        # Case-insensitive name/version; NULLS NOT DISTINCT so org-wide rows stay unique.
        Index(
            "uq_artifacts_org_type_name_version",
            "organization_id",
            "device_type_id",
            "type",
            func.lower(name),
            func.lower(version),
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
        Index("ix_artifacts_organization_id", "organization_id"),
        Index("ix_artifacts_org_device_type_id", "organization_id", "device_type_id"),
        Index("ix_artifacts_org_type", "organization_id", "type"),
    )
