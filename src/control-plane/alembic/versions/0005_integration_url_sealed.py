"""Integration URLs are sealed: notification_integrations.url_sealed.

Rows written before keep their URL in ``url`` until they are saved again.

Revision ID: 0005_integration_url_sealed
Revises: 0004_notification_channels
Create Date: 2026-11-25 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_integration_url_sealed"
down_revision: str | Sequence[str] | None = "0004_notification_channels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notification_integrations", sa.Column("url_sealed", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    # The sealed URLs cannot be read here; their integrations would point at a bare host.
    op.execute("DELETE FROM notification_integrations WHERE url_sealed IS NOT NULL")
    op.drop_column("notification_integrations", "url_sealed")
