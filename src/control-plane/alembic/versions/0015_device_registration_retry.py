"""device_registration_retry: keep previous credential briefly for lost-response retry.

Revision ID: 0015_device_registration_retry
Revises: 0014_device_reregistration
Create Date: 2026-11-16 13:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_device_registration_retry"
down_revision: str | Sequence[str] | None = "0014_device_reregistration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("previous_credential_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "devices",
        sa.Column("previous_credential_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devices", "previous_credential_until")
    op.drop_column("devices", "previous_credential_hash")
