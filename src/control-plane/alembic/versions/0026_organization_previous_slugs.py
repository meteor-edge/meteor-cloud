"""organization_previous_slugs: slugs an organization has renamed away from.

Public app addresses contain the organization slug and stay as they are when it is
renamed. Keeping the old slug reserved stops another organization from publishing
under a name that visitors still associate with this one.

Revision ID: 0026_org_previous_slugs
Revises: 0025_deploy_device_desired_rev
Create Date: 2026-11-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_org_previous_slugs"
down_revision: str | Sequence[str] | None = "0025_deploy_device_desired_rev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_previous_slugs",
        sa.Column("slug", sa.String(length=100), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_organization_previous_slugs_organization_id",
        "organization_previous_slugs",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_organization_previous_slugs_organization_id", table_name="organization_previous_slugs")
    op.drop_table("organization_previous_slugs")
