"""Per-device desired vs installed software.

Revision ID: 0015_device_software
Revises: 0014_releases
Create Date: 2026-10-16 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_device_software"
down_revision: str | Sequence[str] | None = "0014_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_software",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("workload_key", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("desired_release_id", sa.UUID(), nullable=True),
        sa.Column("desired_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "desired_spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("installed_release_id", sa.UUID(), nullable=True),
        sa.Column("installed_version", sa.String(length=64), nullable=True),
        sa.Column("health", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("sync_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("progress_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["desired_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["installed_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "workload_key", name="uq_device_software_device_workload"),
    )
    op.create_index("ix_device_software_organization_id", "device_software", ["organization_id"])
    op.create_index("ix_device_software_device_id", "device_software", ["device_id"])
    op.create_index("ix_device_software_desired_release_id", "device_software", ["desired_release_id"])


def downgrade() -> None:
    op.drop_index("ix_device_software_desired_release_id", table_name="device_software")
    op.drop_index("ix_device_software_device_id", table_name="device_software")
    op.drop_index("ix_device_software_organization_id", table_name="device_software")
    op.drop_table("device_software")
