"""device.logs permission and log sessions.

Log sessions reuse the shell session table (tickets, per-user limits, closing on
revoke): ``kind`` is ``shell`` or ``logs`` and ``params`` holds the validated log
source and options.
device.logs is read-only, so operators and developers get it too (device.shell
stays owner/admin only).

Revision ID: 0023_device_logs
Revises: 0022_sync_authorization_catalog
Create Date: 2026-10-26 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_device_logs"
down_revision: str | Sequence[str] | None = "0022_sync_authorization_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION = ("device", "logs", "View device logs", "Read service, container and system logs on a device.")
ROLES = ("owner", "admin", "operator", "developer")


def upgrade() -> None:
    op.add_column(
        "device_shell_sessions",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="shell"),
    )
    op.add_column(
        "device_shell_sessions",
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    bind = op.get_bind()
    resource, action, label, description = PERMISSION
    bind.execute(
        sa.text(
            "INSERT INTO permissions (id, resource, action, label, description)"
            " VALUES (gen_random_uuid(), :resource, :action, :label, :description)"
            " ON CONFLICT (resource, action)"
            " DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description"
        ),
        {"resource": resource, "action": action, "label": label, "description": description},
    )
    bind.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id)"
            " SELECT roles.id, permissions.id FROM roles, permissions"
            " WHERE roles.organization_id IS NULL AND roles.key = ANY(:roles)"
            " AND permissions.resource = :resource AND permissions.action = :action"
            " ON CONFLICT DO NOTHING"
        ),
        {"roles": list(ROLES), "resource": resource, "action": action},
    )


def downgrade() -> None:
    bind = op.get_bind()
    resource, action, _, _ = PERMISSION
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN"
            " (SELECT id FROM permissions WHERE resource = :resource AND action = :action)"
        ),
        {"resource": resource, "action": action},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE resource = :resource AND action = :action"),
        {"resource": resource, "action": action},
    )
    op.drop_column("device_shell_sessions", "params")
    op.drop_column("device_shell_sessions", "kind")
