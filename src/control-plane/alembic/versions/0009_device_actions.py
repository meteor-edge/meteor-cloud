"""device_actions and the device.users permission: restart, shut down, SSH users.

Revision ID: 0009_device_actions
Revises: 0008_device_uplink
Create Date: 2026-11-12 12:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.sql import false as sa_false

from alembic import op

revision: str = "0009_device_actions"
down_revision: str | Sequence[str] | None = "0008_device_uplink"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SSH password changes are as sensitive as a shell: owners and admins only.
ROLES = ("owner", "admin")


def upgrade() -> None:
    op.create_table(
        "device_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=True),
        sa.Column("sudo", sa.Boolean(), server_default=sa_false(), nullable=False),
        sa.Column("authorized_key", sa.Text(), nullable=True),
        sa.Column("password_sealed", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_actions_device_id", "device_actions", ["device_id"])

    op.get_bind().execute(
        sa.text(
            "UPDATE permissions SET label = :label, description = :description "
            "WHERE resource = 'device' AND action = 'reboot'"
        ),
        {
            "label": "Restart and shut down devices",
            "description": "Restart or shut down a device from the console.",
        },
    )

    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM permissions WHERE resource = 'device' AND action = 'users'")
    ).scalar()
    permission_id = existing or uuid.uuid4()
    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, label, description) "
                "VALUES (:id, 'device', 'users', :label, :description)"
            ),
            {
                "id": permission_id,
                "label": "Manage device SSH users",
                "description": (
                    "Change a user's password or add an SSH user on a device. As sensitive as a shell session."
                ),
            },
        )
    else:
        bind.execute(
            sa.text("UPDATE permissions SET label = :label, description = :description WHERE id = :id"),
            {
                "id": permission_id,
                "label": "Manage device SSH users",
                "description": (
                    "Change a user's password or add an SSH user on a device. As sensitive as a shell session."
                ),
            },
        )
    bind.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT id, :permission_id FROM roles WHERE is_system AND organization_id IS NULL AND key IN :keys "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM role_permissions rp "
            "  WHERE rp.role_id = roles.id AND rp.permission_id = :permission_id"
            ")"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"permission_id": permission_id, "keys": list(ROLES)},
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE resource = 'device' AND action = 'users'")
    op.drop_index("ix_device_actions_device_id", table_name="device_actions")
    op.drop_table("device_actions")
