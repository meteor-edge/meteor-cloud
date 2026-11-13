"""applications and application_credentials: application identities for the data plane.

Revision ID: 0012_applications
Revises: 0011_device_local_ip
Create Date: 2026-11-12 00:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_applications"
down_revision: str | Sequence[str] | None = "0011_device_local_ip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (action, label, description, system roles that get it)
PERMISSIONS = (
    ("read", "View applications", "List applications and their credentials.",
     ("owner", "admin", "operator", "developer", "viewer")),
    ("create", "Create applications", "Register applications for the data plane.",
     ("owner", "admin", "developer")),
    ("update", "Update applications", "Change application details and stream permissions.",
     ("owner", "admin", "developer")),
    ("delete", "Delete applications", "Delete applications and their credentials.",
     ("owner", "admin", "developer")),
    ("credential", "Manage application credentials",
     "Create, rotate and revoke data-plane credentials for applications.",
     ("owner", "admin", "developer")),
    ("command", "Send application commands", "Send commands to running applications.",
     ("owner", "admin", "operator", "developer")),
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.UniqueConstraint("organization_id", "slug", name="uq_applications_org_slug"),
    )
    op.create_index("ix_applications_organization_id", "applications", ["organization_id"])

    op.create_table(
        "application_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("environment", sa.String(32), server_default="production", nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workload_key", sa.String(255), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.UniqueConstraint("token_hash", name="uq_application_credentials_token_hash"),
    )
    op.create_index(
        "ix_application_credentials_application_id", "application_credentials", ["application_id"]
    )
    op.create_index(
        "ix_application_credentials_organization_id", "application_credentials", ["organization_id"]
    )
    op.create_index("ix_application_credentials_device_id", "application_credentials", ["device_id"])

    bind = op.get_bind()
    for action, label, description, roles in PERMISSIONS:
        permission_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, label, description) "
                "VALUES (:id, 'application', :action, :label, :description)"
            ),
            {"id": permission_id, "action": action, "label": label, "description": description},
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT id, :permission_id FROM roles WHERE is_system AND organization_id IS NULL AND key IN :keys"
            ).bindparams(sa.bindparam("keys", expanding=True)),
            {"permission_id": permission_id, "keys": list(roles)},
        )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE resource = 'application'")
    op.drop_table("application_credentials")
    op.drop_table("applications")
