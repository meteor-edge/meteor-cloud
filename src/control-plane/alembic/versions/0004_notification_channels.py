"""Email and Telegram channels: notification_integrations.config.

Revision ID: 0004_notification_channels
Revises: 0003_rules
Create Date: 2026-11-24 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_notification_channels"
down_revision: str | Sequence[str] | None = "0003_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_integrations",
        sa.Column("config", postgresql.JSONB(), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_integrations WHERE kind IN ('email', 'telegram')")
    op.drop_column("notification_integrations", "config")
