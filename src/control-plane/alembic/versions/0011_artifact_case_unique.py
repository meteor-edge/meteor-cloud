"""Replace case-sensitive artifact uniqueness with lower(name)/lower(version).

Revision ID: 0011_artifact_case_unique
Revises: 0010_device_catalog_artifacts
Create Date: 2026-10-07 17:00:00.000000

Databases that already applied the original 0010 unique constraint still need
this conversion. Fresh installs that run the updated 0010 already have the
functional index; this revision is then a no-op rebuild of the same index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_artifact_case_unique"
down_revision: str | Sequence[str] | None = "0010_device_catalog_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the old unique constraint/index and create the case-insensitive one."""
    # UniqueConstraint and unique Index share the name; drop whichever exists.
    op.execute("ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS uq_artifacts_org_type_name_version")
    op.execute("DROP INDEX IF EXISTS uq_artifacts_org_type_name_version")
    op.create_index(
        "uq_artifacts_org_type_name_version",
        "artifacts",
        [
            "organization_id",
            "device_type_id",
            "type",
            sa.text("lower(name)"),
            sa.text("lower(version)"),
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Restore the case-sensitive unique constraint from the original 0010."""
    op.drop_index("uq_artifacts_org_type_name_version", table_name="artifacts")
    op.create_unique_constraint(
        "uq_artifacts_org_type_name_version",
        "artifacts",
        ["organization_id", "device_type_id", "type", "name", "version"],
        postgresql_nulls_not_distinct=True,
    )
