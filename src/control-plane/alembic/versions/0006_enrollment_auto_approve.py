"""Auto-approve rules on enrollment API keys.

Revision ID: 0006_enrollment_auto_approve
Revises: 0005_integration_url_sealed
Create Date: 2026-11-19 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_enrollment_auto_approve"
down_revision: str | Sequence[str] | None = "0005_integration_url_sealed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "enrollment_api_keys",
        sa.Column("auto_approve", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "enrollment_api_keys",
        sa.Column(
            "auto_approve_device_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_types.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "enrollment_api_keys",
        sa.Column(
            "auto_approve_device_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "enrollment_api_keys",
        sa.Column(
            "auto_approve_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("enrollment_api_keys", sa.Column("auto_approve_max", sa.Integer(), nullable=True))
    op.add_column(
        "enrollment_api_keys",
        sa.Column("auto_approve_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "enrollment_api_keys",
        sa.Column("auto_approve_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device_enrollment_requests",
        sa.Column("auto_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("device_enrollment_requests", "auto_approved")
    for column in (
        "auto_approve_until",
        "auto_approve_count",
        "auto_approve_max",
        "auto_approve_allowlist",
        "auto_approve_device_group_id",
        "auto_approve_device_type_id",
        "auto_approve",
    ):
        op.drop_column("enrollment_api_keys", column)
