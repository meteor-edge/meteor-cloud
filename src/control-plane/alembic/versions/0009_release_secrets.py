"""Release secrets: encrypted environment values, delivered to devices separately from the spec.

Revision ID: 0009_release_secrets
Revises: 0008_container_images
Create Date: 2026-11-25 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_release_secrets"
down_revision: str | Sequence[str] | None = "0008_container_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_secrets",
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(128), primary_key=True),
        sa.Column("sealed_value", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("release_secrets")
