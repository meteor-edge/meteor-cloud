"""Record whether a device trusts the SSH user CA (short-lived certificates).

Revision ID: 0021_connectivity_ssh_auth
Revises: 0020_connectivity_host_keys
Create Date: 2026-10-24 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_connectivity_ssh_auth"
down_revision: str | Sequence[str] | None = "0020_connectivity_host_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device_connectivity", sa.Column("ssh_auth", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("device_connectivity", "ssh_auth")
