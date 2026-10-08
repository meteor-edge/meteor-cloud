"""RBAC permissions, roles, membership role_id, and access bindings.

Revision ID: 0012_rbac_permissions
Revises: 0011_artifact_case_unique
Create Date: 2026-10-07 17:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_rbac_permissions"
down_revision: str | Sequence[str] | None = "0011_artifact_case_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_ROLE_MAP = {
    "owner": "owner",
    "admin": "admin",
    "member": "operator",
    "viewer": "viewer",
}


def upgrade() -> None:
    """Create RBAC tables, seed system roles, and migrate memberships."""
    op.create_table(
        "permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "key",
            name="uq_roles_org_key",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_roles_organization_id", "roles", ["organization_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )

    # Seed catalog using the application definitions (same source of truth as runtime).
    from sqlalchemy.orm import Session

    from app.authorization.seed import seed_authorization_catalog

    bind = op.get_bind()
    session = Session(bind=bind)
    role_ids = seed_authorization_catalog(session)
    session.commit()

    op.add_column("organization_memberships", sa.Column("role_id", sa.UUID(), nullable=True))
    op.add_column(
        "organization_memberships",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )

    memberships = bind.execute(sa.text("SELECT id, role::text AS role FROM organization_memberships")).mappings()
    for row in memberships:
        key = _LEGACY_ROLE_MAP[row["role"]]
        bind.execute(
            sa.text("UPDATE organization_memberships SET role_id = :role_id WHERE id = :id"),
            {"role_id": role_ids[key], "id": row["id"]},
        )

    op.alter_column("organization_memberships", "role_id", nullable=False)
    op.create_foreign_key(
        "fk_organization_memberships_role_id_roles",
        "organization_memberships",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_organization_memberships_role_id", "organization_memberships", ["role_id"], unique=False)

    op.drop_column("organization_memberships", "role")
    op.execute("DROP TYPE IF EXISTS organization_role")

    op.create_table(
        "access_bindings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["membership_id"], ["organization_memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id",
            "scope_type",
            "scope_id",
            name="uq_access_bindings_membership_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_access_bindings_membership_id", "access_bindings", ["membership_id"], unique=False)
    op.create_index("ix_access_bindings_scope", "access_bindings", ["scope_type", "scope_id"], unique=False)


def downgrade() -> None:
    """Restore the enum role column and drop RBAC tables."""
    op.drop_index("ix_access_bindings_scope", table_name="access_bindings")
    op.drop_index("ix_access_bindings_membership_id", table_name="access_bindings")
    op.drop_table("access_bindings")

    organization_role = postgresql.ENUM(
        "owner",
        "admin",
        "member",
        "viewer",
        name="organization_role",
        create_type=False,
    )
    organization_role.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "organization_memberships",
        sa.Column("role", organization_role, nullable=True),
    )

    reverse = {"owner": "owner", "admin": "admin", "operator": "member", "developer": "member", "viewer": "viewer"}
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT m.id, r.key
            FROM organization_memberships m
            JOIN roles r ON r.id = m.role_id
            """
        )
    ).mappings()
    for row in rows:
        op.get_bind().execute(
            sa.text("UPDATE organization_memberships SET role = :role WHERE id = :id"),
            {"role": reverse[row["key"]], "id": row["id"]},
        )

    op.alter_column("organization_memberships", "role", nullable=False)
    op.drop_index("ix_organization_memberships_role_id", table_name="organization_memberships")
    op.drop_constraint("fk_organization_memberships_role_id_roles", "organization_memberships", type_="foreignkey")
    op.drop_column("organization_memberships", "role_id")
    op.drop_column("organization_memberships", "status")

    op.drop_table("role_permissions")
    op.drop_index("ix_roles_organization_id", table_name="roles")
    op.drop_table("roles")
    op.drop_table("permissions")
