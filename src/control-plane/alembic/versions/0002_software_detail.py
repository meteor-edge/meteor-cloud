"""Software detail view: workload snapshots, stopped state, workload actions, software.manage.

Revision ID: 0002_software_detail
Revises: 0001_initial
Create Date: 2026-11-24 12:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_software_detail"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("owner", "admin", "operator")


def upgrade() -> None:
    op.add_column("device_software", sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("device_software", sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "device_software",
        sa.Column("stopped_by_user", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.alter_column("device_actions", "kind", existing_type=sa.String(16), type_=sa.String(32), existing_nullable=False)
    op.add_column("device_actions", sa.Column("workload_key", sa.String(255), nullable=True))
    op.add_column("device_actions", sa.Column("service", sa.String(255), nullable=True))

    bind = op.get_bind()
    permission_id = uuid.uuid4()
    bind.execute(
        sa.text(
            "INSERT INTO permissions (id, resource, action, label, description) "
            "VALUES (:id, 'software', 'manage', 'Manage device software', "
            "'Restart, stop and start software deployed to a device.')"
        ),
        {"id": permission_id},
    )
    bind.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT id, :id FROM roles WHERE is_system AND organization_id IS NULL AND key = ANY(:keys)"
        ),
        {"id": permission_id, "keys": list(ROLES)},
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE resource = 'software' AND action = 'manage')"
    )
    op.execute("DELETE FROM permissions WHERE resource = 'software' AND action = 'manage'")
    op.execute("DELETE FROM device_actions WHERE kind LIKE 'workload!_%' ESCAPE '!'")
    op.drop_column("device_actions", "service")
    op.drop_column("device_actions", "workload_key")
    op.alter_column("device_actions", "kind", existing_type=sa.String(32), type_=sa.String(16), existing_nullable=False)
    op.drop_column("device_software", "stopped_by_user")
    op.drop_column("device_software", "snapshot_at")
    op.drop_column("device_software", "snapshot")
