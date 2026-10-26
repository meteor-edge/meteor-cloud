"""Store the SSH user the device agent reported for browser SSH.

Revision ID: 0019_connectivity_ssh_user
Revises: 0018_connectivity_endpoints
Create Date: 2026-10-24 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_connectivity_ssh_user"
down_revision: str | Sequence[str] | None = "0018_connectivity_endpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "device_connectivity",
        sa.Column("ssh_user", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_connectivity", "ssh_user")
