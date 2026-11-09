"""artifacts.published: OS images listed and downloadable without login, for Meteor Imager.

Revision ID: 0002_artifact_published
Revises: 0001_initial
Create Date: 2026-11-09 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_artifact_published"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("published", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_check_constraint("ck_artifacts_published_os_image", "artifacts", "NOT published OR type = 'os_image'")


def downgrade() -> None:
    op.drop_constraint("ck_artifacts_published_os_image", "artifacts", type_="check")
    op.drop_column("artifacts", "published")
