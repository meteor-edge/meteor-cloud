"""Organization domain models."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.authorization.models import AccessBinding, MembershipStatus, Role
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrganizationRole(enum.StrEnum):
    """System role keys exposed on APIs (replaces the old member enum value)."""

    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tenant organization."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )


class OrganizationMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Membership of a user in an organization."""

    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MembershipStatus.ACTIVE.value,
        server_default=MembershipStatus.ACTIVE.value,
    )

    role_ref: Mapped[Role] = relationship(Role, lazy="joined")
    bindings: Mapped[list[AccessBinding]] = relationship(
        AccessBinding,
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def role(self) -> OrganizationRole:
        """Compatibility shim: role key as OrganizationRole."""
        return OrganizationRole(self.role_ref.key)

    @property
    def role_key(self) -> str:
        return self.role_ref.key


class Team(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named group of members in one organization. Grants no permissions in v1."""

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    members: Mapped[list[TeamMember]] = relationship(
        "TeamMember",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TeamMember(Base, TimestampMixin):
    """Links a member (organization membership) to a team."""

    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
