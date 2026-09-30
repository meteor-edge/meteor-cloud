"""Drop machine_id; devices are identified by id, serial, and MAC addresses.

Revision ID: 0008_drop_machine_id
Revises: 0007_mqtt
Create Date: 2026-08-21 09:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_drop_machine_id"
down_revision: str | Sequence[str] | None = "0007_mqtt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_devices_org_machine_id", table_name="devices")
    op.drop_column("devices", "machine_id")
    op.drop_column("device_enrollment_requests", "machine_id")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column("devices", sa.Column("machine_id", sa.String(length=255), nullable=True))
    op.add_column(
        "device_enrollment_requests",
        sa.Column("machine_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_devices_org_machine_id",
        "devices",
        ["organization_id", "machine_id"],
        unique=False,
    )
