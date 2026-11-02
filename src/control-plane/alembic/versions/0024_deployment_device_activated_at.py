"""deployment_devices.activated_at and a (deployment_id, status) index.

activated_at records when desired software was written for a device, so rollback
and phased rollouts know which devices actually received a release. Existing rows
are backfilled: batched Deploys use rollout.activated_device_ids and
rollout.batch_released_at; other Deploys mark every row that left queued/skipped
at the Deploy's created_at.

Revision ID: 0024_deploy_device_activated_at
Revises: 0023_device_logs
Create Date: 2026-11-02 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_deploy_device_activated_at"
down_revision: str | Sequence[str] | None = "0023_device_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployment_devices",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_deployment_devices_deployment_status",
        "deployment_devices",
        ["deployment_id", "status"],
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE deployment_devices dd"
            " SET activated_at = COALESCE(NULLIF(d.rollout->>'batch_released_at', '')::timestamptz,"
            " d.created_at)"
            " FROM deployments d"
            " WHERE d.id = dd.deployment_id"
            " AND d.rollout->>'strategy' = 'batched'"
            " AND jsonb_typeof(d.rollout->'activated_device_ids') = 'array'"
            " AND d.rollout->'activated_device_ids' ? dd.device_id::text"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE deployment_devices dd"
            " SET activated_at = d.created_at"
            " FROM deployments d"
            " WHERE d.id = dd.deployment_id"
            " AND COALESCE(d.rollout->>'strategy', 'immediate') <> 'batched'"
            " AND dd.status NOT IN ('queued', 'skipped')"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_devices_deployment_status", table_name="deployment_devices")
    op.drop_column("deployment_devices", "activated_at")
