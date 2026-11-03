"""deployment_devices.desired_revision: the device revision this Deploy wrote.

Cancel only restores or clears desired software that this Deploy still owns. Matching on
the release id is not enough: a newer Deploy of the same release writes the same release
id, so cancelling the older one would undo the newer one. Rows written before this
migration stay null and fall back to the release-id check.

Revision ID: 0025_deploy_device_desired_rev
Revises: 0024_deploy_device_activated_at
Create Date: 2026-11-03 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_deploy_device_desired_rev"
down_revision: str | Sequence[str] | None = "0024_deploy_device_activated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployment_devices",
        sa.Column("desired_revision", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployment_devices", "desired_revision")
