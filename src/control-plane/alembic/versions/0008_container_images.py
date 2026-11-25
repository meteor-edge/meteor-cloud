"""Container images: the tags in the registry, for listing.

Revision ID: 0008_container_images
Revises: 0007_registry
Create Date: 2026-11-25 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_container_images"
down_revision: str | Sequence[str] | None = "0007_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "container_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("platforms", postgresql.JSONB(), nullable=False),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pushed_by_token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("registry_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "repository", "tag", name="uq_container_images_repository_tag"),
    )
    op.create_index("ix_container_images_organization_id_digest", "container_images", ["organization_id", "digest"])


def downgrade() -> None:
    op.drop_table("container_images")
