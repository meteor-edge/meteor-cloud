"""Deployments: history of Deploy actions and per-device progress.

Revision ID: 0016_deployments
Revises: 0015_device_software
Create Date: 2026-10-16 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_deployments"
down_revision: str | Sequence[str] | None = "0015_device_software"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("release_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "target",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "rollout",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{"strategy": "immediate"}',
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="in_progress", nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("rollback_of_deployment_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rollback_of_deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployments_organization_id", "deployments", ["organization_id"])
    op.create_index("ix_deployments_org_status", "deployments", ["organization_id", "status"])
    op.create_index("ix_deployments_release_id", "deployments", ["release_id"])

    op.create_table(
        "deployment_devices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("previous_desired_release_id", sa.UUID(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_desired_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", "device_id", name="uq_deployment_devices_deployment_device"),
    )
    op.create_index("ix_deployment_devices_deployment_id", "deployment_devices", ["deployment_id"])
    op.create_index("ix_deployment_devices_device_id", "deployment_devices", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_deployment_devices_device_id", table_name="deployment_devices")
    op.drop_index("ix_deployment_devices_deployment_id", table_name="deployment_devices")
    op.drop_table("deployment_devices")
    op.drop_index("ix_deployments_release_id", table_name="deployments")
    op.drop_index("ix_deployments_org_status", table_name="deployments")
    op.drop_index("ix_deployments_organization_id", table_name="deployments")
    op.drop_table("deployments")
