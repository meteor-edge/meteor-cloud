"""local_ip: the device's LAN address, reported with inventory.

Revision ID: 0011_device_local_ip
Revises: 0010_reboot_owners_admins
Create Date: 2026-11-12 14:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_device_local_ip"
down_revision: str | Sequence[str] | None = "0010_reboot_owners_admins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("devices", "device_enrollment_requests")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("local_ip", sa.String(length=64), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "local_ip")
