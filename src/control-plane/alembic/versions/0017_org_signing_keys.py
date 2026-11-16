"""org_signing_keys: per-organization Ed25519 keys that sign desired software.

Revision ID: 0017_org_signing_keys
Revises: 0016_user_token_version
Create Date: 2026-11-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_org_signing_keys"
down_revision: str | Sequence[str] | None = "0016_user_token_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_signing_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_id", sa.String(32), nullable=False, unique=True),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("sealed_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("endorsed_by", sa.String(32), nullable=True),
        sa.Column("endorsement", sa.String(128), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_organization_signing_keys_organization_id", "organization_signing_keys", ["organization_id"]
    )
    op.create_index(
        "uq_organization_signing_keys_active",
        "organization_signing_keys",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_organization_signing_keys_active", table_name="organization_signing_keys")
    op.drop_index("ix_organization_signing_keys_organization_id", table_name="organization_signing_keys")
    op.drop_table("organization_signing_keys")
