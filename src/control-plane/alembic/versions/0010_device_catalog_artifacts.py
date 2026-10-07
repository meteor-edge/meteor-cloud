"""Device catalog fields (slug, hardware details, metadata) and artifacts.

Revision ID: 0010_device_catalog_artifacts
Revises: 0009_mqtt_metrics
Create Date: 2026-10-07 10:00:00.000000
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_device_catalog_artifacts"
down_revision: str | Sequence[str] | None = "0009_mqtt_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_column(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False)


def _backfill_slugs(table: str, fallback: str) -> None:
    """Derive a slug from the name, unique per organization (suffix -2, -3, …)."""
    connection = op.get_bind()
    rows = connection.execute(sa.text(f"SELECT id, organization_id, name FROM {table} ORDER BY created_at, id")).all()
    taken: set[tuple[str, str]] = set()
    for row in rows:
        base = re.sub(r"[^a-z0-9]+", "-", row.name.strip().lower()).strip("-")[:90].strip("-") or fallback
        slug, suffix = base, 2
        while (str(row.organization_id), slug) in taken:
            slug = f"{base}-{suffix}"
            suffix += 1
        taken.add((str(row.organization_id), slug))
        connection.execute(sa.text(f"UPDATE {table} SET slug = :slug WHERE id = :id"), {"slug": slug, "id": row.id})


def upgrade() -> None:
    op.add_column("device_types", sa.Column("slug", sa.String(length=120), nullable=True))
    op.add_column("device_types", sa.Column("manufacturer", sa.String(length=120), nullable=True))
    op.add_column("device_types", sa.Column("model", sa.String(length=120), nullable=True))
    op.add_column("device_types", sa.Column("architecture", sa.String(length=64), nullable=True))
    op.add_column("device_types", _jsonb_column("metadata"))
    _backfill_slugs("device_types", "device-type")
    op.alter_column("device_types", "slug", nullable=False)
    op.create_unique_constraint("uq_device_types_org_slug", "device_types", ["organization_id", "slug"])

    op.add_column("device_groups", sa.Column("slug", sa.String(length=120), nullable=True))
    op.add_column("device_groups", _jsonb_column("metadata"))
    _backfill_slugs("device_groups", "device-group")
    op.alter_column("device_groups", "slug", nullable=False)
    op.create_unique_constraint("uq_device_groups_org_slug", "device_groups", ["organization_id", "slug"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        _jsonb_column("metadata"),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "device_type_id",
            "type",
            "name",
            "version",
            name="uq_artifacts_org_type_name_version",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
    )
    op.create_index("ix_artifacts_organization_id", "artifacts", ["organization_id"], unique=False)
    op.create_index(
        "ix_artifacts_org_device_type_id",
        "artifacts",
        ["organization_id", "device_type_id"],
        unique=False,
    )
    op.create_index("ix_artifacts_org_type", "artifacts", ["organization_id", "type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_artifacts_org_type", table_name="artifacts")
    op.drop_index("ix_artifacts_org_device_type_id", table_name="artifacts")
    op.drop_index("ix_artifacts_organization_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_constraint("uq_device_groups_org_slug", "device_groups", type_="unique")
    op.drop_column("device_groups", "metadata")
    op.drop_column("device_groups", "slug")

    op.drop_constraint("uq_device_types_org_slug", "device_types", type_="unique")
    op.drop_column("device_types", "metadata")
    op.drop_column("device_types", "architecture")
    op.drop_column("device_types", "model")
    op.drop_column("device_types", "manufacturer")
    op.drop_column("device_types", "slug")
