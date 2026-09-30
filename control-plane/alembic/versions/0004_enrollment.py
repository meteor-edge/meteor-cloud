"""Add device-initiated enrollment: API keys and enrollment requests.

Revision ID: 0004_enrollment
Revises: 0003_fleet_foundation
Create Date: 2026-08-18 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_enrollment"
down_revision: str | Sequence[str] | None = "0003_fleet_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrollment_api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_group_id"], ["device_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_enrollment_api_keys_key_hash"),
    )
    op.create_index(
        "ix_enrollment_api_keys_organization_id",
        "enrollment_api_keys",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "device_enrollment_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("api_key_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("claim_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("claim_secret_prefix", sa.String(length=32), nullable=False),
        sa.Column("requested_name", sa.String(length=255), nullable=True),
        sa.Column("assigned_name", sa.String(length=255), nullable=True),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("machine_id", sa.String(length=255), nullable=True),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column(
            "mac_addresses",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("os_name", sa.String(length=255), nullable=True),
        sa.Column("os_version", sa.String(length=255), nullable=True),
        sa.Column("kernel_version", sa.String(length=255), nullable=True),
        sa.Column("architecture", sa.String(length=64), nullable=True),
        sa.Column("cpu_model", sa.String(length=255), nullable=True),
        sa.Column("cpu_cores", sa.Integer(), nullable=True),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["api_key_id"], ["enrollment_api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_group_id"], ["device_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_enrollment_requests_organization_id",
        "device_enrollment_requests",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_enrollment_requests_org_status",
        "device_enrollment_requests",
        ["organization_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_enrollment_requests_org_status",
        table_name="device_enrollment_requests",
    )
    op.drop_index(
        "ix_device_enrollment_requests_organization_id",
        table_name="device_enrollment_requests",
    )
    op.drop_table("device_enrollment_requests")

    op.drop_index(
        "ix_enrollment_api_keys_organization_id",
        table_name="enrollment_api_keys",
    )
    op.drop_table("enrollment_api_keys")
