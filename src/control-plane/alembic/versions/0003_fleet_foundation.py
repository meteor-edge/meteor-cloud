"""Add fleet foundation: device types, groups, registration tokens, devices.

Revision ID: 0003_fleet_foundation
Revises: 0002_identity_organizations
Create Date: 2026-08-18 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_fleet_foundation"
down_revision: str | Sequence[str] | None = "0002_identity_organizations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_types",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_device_types_org_name"),
    )
    op.create_index(
        "ix_device_types_organization_id",
        "device_types",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "device_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_device_groups_org_name"),
    )
    op.create_index(
        "ix_device_groups_organization_id",
        "device_groups",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "registration_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("token_hash", name="uq_registration_tokens_token_hash"),
    )
    op.create_index(
        "ix_registration_tokens_organization_id",
        "registration_tokens",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
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
        sa.Column("credential_hash", sa.String(length=64), nullable=True),
        sa.Column("credential_prefix", sa.String(length=32), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_token_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["registration_token_id"],
            ["registration_tokens.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash", name="uq_devices_credential_hash"),
    )
    op.create_index("ix_devices_organization_id", "devices", ["organization_id"], unique=False)
    op.create_index(
        "ix_devices_org_machine_id",
        "devices",
        ["organization_id", "machine_id"],
        unique=False,
    )
    op.create_index(
        "ix_devices_org_serial_number",
        "devices",
        ["organization_id", "serial_number"],
        unique=False,
    )
    op.create_index(
        "ix_devices_org_last_seen_at",
        "devices",
        ["organization_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_devices_org_device_type_id",
        "devices",
        ["organization_id", "device_type_id"],
        unique=False,
    )
    op.create_index(
        "ix_devices_org_device_group_id",
        "devices",
        ["organization_id", "device_group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_devices_org_device_group_id", table_name="devices")
    op.drop_index("ix_devices_org_device_type_id", table_name="devices")
    op.drop_index("ix_devices_org_last_seen_at", table_name="devices")
    op.drop_index("ix_devices_org_serial_number", table_name="devices")
    op.drop_index("ix_devices_org_machine_id", table_name="devices")
    op.drop_index("ix_devices_organization_id", table_name="devices")
    op.drop_table("devices")

    op.drop_index("ix_registration_tokens_organization_id", table_name="registration_tokens")
    op.drop_table("registration_tokens")

    op.drop_index("ix_device_groups_organization_id", table_name="device_groups")
    op.drop_table("device_groups")

    op.drop_index("ix_device_types_organization_id", table_name="device_types")
    op.drop_table("device_types")
