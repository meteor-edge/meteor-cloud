"""Pin device SSH host keys and keep the last browser SSH outcome.

Revision ID: 0020_connectivity_host_keys
Revises: 0019_connectivity_ssh_user
Create Date: 2026-10-24 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_connectivity_host_keys"
down_revision: str | Sequence[str] | None = "0019_connectivity_ssh_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device_connectivity", sa.Column("ssh_host_key", sa.String(length=2048), nullable=True))
    op.add_column("device_connectivity", sa.Column("last_ssh_ok_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("device_connectivity", sa.Column("last_ssh_error", sa.String(length=255), nullable=True))
    op.add_column("device_connectivity", sa.Column("last_ssh_error_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("device_connectivity", "last_ssh_error_at")
    op.drop_column("device_connectivity", "last_ssh_error")
    op.drop_column("device_connectivity", "last_ssh_ok_at")
    op.drop_column("device_connectivity", "ssh_host_key")
