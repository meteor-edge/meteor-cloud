"""hardware_model: the board model a device reports, for default names and device type hints.

Revision ID: 0005_hardware_model
Revises: 0004_audit_actor
Create Date: 2026-11-11 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_hardware_model"
down_revision: str | Sequence[str] | None = "0004_audit_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("devices", "device_enrollment_requests")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("hardware_model", sa.String(length=255), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "hardware_model")
