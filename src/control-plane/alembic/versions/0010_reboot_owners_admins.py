"""device.reboot: owners and admins only (no longer on the operator role).

Revision ID: 0010_reboot_owners_admins
Revises: 0009_device_actions
Create Date: 2026-11-12 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_reboot_owners_admins"
down_revision: str | Sequence[str] | None = "0009_device_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE role_id = (SELECT id FROM roles WHERE is_system AND organization_id IS NULL AND key = 'operator') "
            "AND permission_id = ("
            "  SELECT id FROM permissions WHERE resource = 'device' AND action = 'reboot'"
            ")"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r, permissions p "
            "WHERE r.is_system AND r.organization_id IS NULL AND r.key = 'operator' "
            "AND p.resource = 'device' AND p.action = 'reboot' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM role_permissions rp "
            "  WHERE rp.role_id = r.id AND rp.permission_id = p.id"
            ")"
        )
    )
