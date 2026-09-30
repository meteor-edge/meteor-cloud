"""Drop device type and group defaults from enrollment API keys.

Revision ID: 0005_drop_key_type_group
Revises: 0004_enrollment
Create Date: 2026-08-18 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_drop_key_type_group"
down_revision: str | Sequence[str] | None = "0004_enrollment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys("enrollment_api_keys"):
        if foreign_key["constrained_columns"] in (["device_type_id"], ["device_group_id"]):
            op.drop_constraint(foreign_key["name"], "enrollment_api_keys", type_="foreignkey")
    op.drop_column("enrollment_api_keys", "device_type_id")
    op.drop_column("enrollment_api_keys", "device_group_id")


def downgrade() -> None:
    op.add_column(
        "enrollment_api_keys",
        sa.Column("device_type_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "enrollment_api_keys",
        sa.Column("device_group_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "enrollment_api_keys_device_type_id_fkey",
        "enrollment_api_keys",
        "device_types",
        ["device_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "enrollment_api_keys_device_group_id_fkey",
        "enrollment_api_keys",
        "device_groups",
        ["device_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
