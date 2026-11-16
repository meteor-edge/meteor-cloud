"""device_reregistration: re-registering known hardware needs proof or an admin window.

Revision ID: 0014_device_reregistration
Revises: 0013_platform_event_outbox
Create Date: 2026-11-16 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_device_reregistration"
down_revision: str | Sequence[str] | None = "0013_platform_event_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("reregistration_allowed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devices", "reregistration_allowed_until")
