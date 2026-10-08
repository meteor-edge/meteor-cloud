"""RBAC persistence: permissions, roles, bindings."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ScopeType(enum.StrEnum):
    ORGANIZATION = "organization"
    DEVICE_GROUP = "device_group"


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Permission(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),)

    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    @property
    def key(self) -> str:
        return f"{self.resource}.{self.action}"


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "key",
            name="uq_roles_org_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_roles_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class AccessBinding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Limits a membership to specific device groups.

    Zero bindings means organization-wide access for device-scoped resources.
    """

    __tablename__ = "access_bindings"
    __table_args__ = (
        UniqueConstraint(
            "membership_id",
            "scope_type",
            "scope_id",
            name="uq_access_bindings_membership_scope",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_access_bindings_membership_id", "membership_id"),
        Index("ix_access_bindings_scope", "scope_type", "scope_id"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
