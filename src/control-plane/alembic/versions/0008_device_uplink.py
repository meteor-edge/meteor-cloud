"""device_wifi_status.uplink_type / uplink_interface: the link a device's traffic uses.

Revision ID: 0008_device_uplink
Revises: 0007_device_wifi
Create Date: 2026-11-12 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_device_uplink"
down_revision: str | Sequence[str] | None = "0007_device_wifi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device_wifi_status", sa.Column("uplink_type", sa.String(length=16), nullable=True))
    op.add_column("device_wifi_status", sa.Column("uplink_interface", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("device_wifi_status", "uplink_interface")
    op.drop_column("device_wifi_status", "uplink_type")
