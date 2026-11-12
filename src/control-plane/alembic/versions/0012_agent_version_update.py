"""devices.agent_version and device.update_agent for meteorcli updates.

Revision ID: 0012_agent_version_update
Revises: 0011_device_local_ip
Create Date: 2026-11-12 22:30:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_agent_version_update"
down_revision: str | Sequence[str] | None = "0011_device_local_ip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("owner", "admin")


def upgrade() -> None:
    op.add_column("devices", sa.Column("agent_version", sa.String(length=32), nullable=True))
    op.add_column("device_actions", sa.Column("from_version", sa.String(length=32), nullable=True))

    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM permissions WHERE resource = 'device' AND action = 'update_agent'")
    ).scalar()
    permission_id = existing or uuid.uuid4()
    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, label, description) "
                "VALUES (:id, 'device', 'update_agent', :label, :description)"
            ),
            {
                "id": permission_id,
                "label": "Update device agent",
                "description": "Order meteorcli on a device to install the latest published release.",
            },
        )
    for key in ROLES:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, :permission_id FROM roles r "
                "WHERE r.is_system AND r.organization_id IS NULL AND r.key = :key "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM role_permissions rp "
                "  WHERE rp.role_id = r.id AND rp.permission_id = :permission_id"
                ")"
            ),
            {"permission_id": permission_id, "key": key},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id = ("
            "  SELECT id FROM permissions WHERE resource = 'device' AND action = 'update_agent'"
            ")"
        )
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE resource = 'device' AND action = 'update_agent'"))
    op.drop_column("device_actions", "from_version")
    op.drop_column("devices", "agent_version")
