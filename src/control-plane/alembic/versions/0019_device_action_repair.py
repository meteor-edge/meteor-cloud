"""device_actions.module and .output for restart_module and run_diagnostics.

Revision ID: 0019_device_action_repair
Revises: 0018_agent_version_update
Create Date: 2026-11-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_device_action_repair"
down_revision: str | Sequence[str] | None = "0018_agent_version_update"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device_actions", sa.Column("module", sa.String(length=64), nullable=True))
    op.add_column("device_actions", sa.Column("output", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("device_actions", "output")
    op.drop_column("device_actions", "module")
