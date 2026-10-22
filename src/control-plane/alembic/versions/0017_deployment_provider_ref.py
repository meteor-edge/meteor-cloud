"""Store which deployment engine ran a Deploy and its external id.

Revision ID: 0017_deployment_provider_ref
Revises: 0016_deployments
Create Date: 2026-10-16 19:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_deployment_provider_ref"
down_revision: str | Sequence[str] | None = "0016_deployments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("provider", sa.String(length=32), server_default="native", nullable=False),
    )
    op.add_column(
        "deployments",
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_deployments_provider_ref", "deployments", ["provider", "provider_ref"])


def downgrade() -> None:
    op.drop_index("ix_deployments_provider_ref", table_name="deployments")
    op.drop_column("deployments", "provider_ref")
    op.drop_column("deployments", "provider")
