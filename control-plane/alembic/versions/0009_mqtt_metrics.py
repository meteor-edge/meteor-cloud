"""Store the latest MQTT health metrics snapshot on devices.

Revision ID: 0009_mqtt_metrics
Revises: 0008_drop_machine_id
Create Date: 2026-08-25 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_mqtt_metrics"
down_revision: str | Sequence[str] | None = "0008_drop_machine_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("mqtt_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("mqtt_metrics_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devices", "mqtt_metrics_at")
    op.drop_column("devices", "mqtt_metrics")
