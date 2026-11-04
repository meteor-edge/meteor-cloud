"""Store last-reported host network links (ethernet/wifi) on the device.

Revision ID: 0027_device_network_links
Revises: 0026_device_wifi_settings
Create Date: 2026-11-04 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027_device_network_links"
down_revision: str | Sequence[str] | None = "0026_device_wifi_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("network_links", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devices", "network_links")
