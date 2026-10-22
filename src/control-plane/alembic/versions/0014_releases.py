"""Software release catalog.

Revision ID: 0014_releases
Revises: 0013_teams
Create Date: 2026-10-16 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_releases"
down_revision: str | Sequence[str] | None = "0013_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "releases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("artifact_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_releases_org_kind_name_version",
        "releases",
        [
            "organization_id",
            "kind",
            sa.text("lower(name)"),
            sa.text("lower(version)"),
        ],
        unique=True,
    )
    op.create_index("ix_releases_organization_id", "releases", ["organization_id"], unique=False)
    op.create_index("ix_releases_org_kind", "releases", ["organization_id", "kind"], unique=False)
    op.create_index("ix_releases_artifact_id", "releases", ["artifact_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_releases_artifact_id", table_name="releases")
    op.drop_index("ix_releases_org_kind", table_name="releases")
    op.drop_index("ix_releases_organization_id", table_name="releases")
    op.drop_index("uq_releases_org_kind_name_version", table_name="releases")
    op.drop_table("releases")
