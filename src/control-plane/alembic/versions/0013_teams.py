"""Teams: named groups of organization members, plus team permissions.

Revision ID: 0013_teams
Revises: 0012_rbac_permissions
Create Date: 2026-10-08 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_teams"
down_revision: str | Sequence[str] | None = "0012_rbac_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),
    )
    op.create_index("ix_teams_organization_id", "teams", ["organization_id"], unique=False)
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["organization_memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "membership_id"),
    )
    op.create_index("ix_team_members_membership_id", "team_members", ["membership_id"], unique=False)

    # Add team.* permissions to the seeded catalog and system roles.
    from sqlalchemy.orm import Session

    from app.authorization.seed import seed_authorization_catalog

    session = Session(bind=op.get_bind())
    seed_authorization_catalog(session)
    session.commit()


def downgrade() -> None:
    op.drop_index("ix_team_members_membership_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_index("ix_teams_organization_id", table_name="teams")
    op.drop_table("teams")
    op.execute("DELETE FROM permissions WHERE resource = 'team'")
