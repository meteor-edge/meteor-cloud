"""Software release catalog: a version of software that can be deployed."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.releases.kinds import ReleaseKind


class Release(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named, versioned software package (systemd unit, container, or compose app)."""

    __tablename__ = "releases"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[ReleaseKind] = mapped_column(
        Enum(
            ReleaseKind,
            name="release_kind",
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec: Mapped[dict[str, Any]] = mapped_column(
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
        Index(
            "uq_releases_org_kind_name_version",
            "organization_id",
            "kind",
            func.lower(name),
            func.lower(version),
            unique=True,
        ),
        Index("ix_releases_organization_id", "organization_id"),
        Index("ix_releases_org_kind", "organization_id", "kind"),
        Index("ix_releases_artifact_id", "artifact_id"),
    )
