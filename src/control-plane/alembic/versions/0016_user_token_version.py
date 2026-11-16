"""user_token_version: raising it signs the user out of every session.

Revision ID: 0016_user_token_version
Revises: 0015_device_registration_retry
Create Date: 2026-11-16 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_user_token_version"
down_revision: str | Sequence[str] | None = "0015_device_registration_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "token_version")
