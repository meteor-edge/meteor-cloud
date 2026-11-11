"""artifact_device_types: an artifact can be for several device types (one OS image, several boards).

Revision ID: 0005_artifact_device_types
Revises: 0004_hardware_model
Create Date: 2026-11-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_artifact_device_types"
down_revision: str | Sequence[str] | None = "0004_hardware_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_device_types",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("artifact_id", "device_type_id"),
    )
    op.create_index("ix_artifact_device_types_device_type_id", "artifact_device_types", ["device_type_id"])
    op.execute(
        "INSERT INTO artifact_device_types (artifact_id, device_type_id) "
        "SELECT id, device_type_id FROM artifacts WHERE device_type_id IS NOT NULL"
    )
    op.drop_index("uq_artifacts_org_type_name_version", table_name="artifacts")
    op.drop_index("ix_artifacts_org_device_type_id", table_name="artifacts")
    op.drop_column("artifacts", "device_type_id")
    op.create_index(
        "ix_artifacts_org_type_name_version",
        "artifacts",
        ["organization_id", "type", sa.text("lower(name)"), sa.text("lower(version)")],
    )


def downgrade() -> None:
    """Back to one device type per artifact: an artifact with several keeps the first by name."""
    op.drop_index("ix_artifacts_org_type_name_version", table_name="artifacts")
    op.add_column("artifacts", sa.Column("device_type_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "artifacts_device_type_id_fkey", "artifacts", "device_types", ["device_type_id"], ["id"], ondelete="RESTRICT"
    )
    op.execute(
        "UPDATE artifacts SET device_type_id = ("
        "SELECT adt.device_type_id FROM artifact_device_types adt "
        "JOIN device_types dt ON dt.id = adt.device_type_id "
        "WHERE adt.artifact_id = artifacts.id ORDER BY dt.name LIMIT 1)"
    )
    op.create_index("ix_artifacts_org_device_type_id", "artifacts", ["organization_id", "device_type_id"])
    # Not unique: artifacts that shared a name and version across different device types may now collide.
    op.create_index(
        "uq_artifacts_org_type_name_version",
        "artifacts",
        ["organization_id", "device_type_id", "type", sa.text("lower(name)"), sa.text("lower(version)")],
    )
    op.drop_index("ix_artifact_device_types_device_type_id", table_name="artifact_device_types")
    op.drop_table("artifact_device_types")
