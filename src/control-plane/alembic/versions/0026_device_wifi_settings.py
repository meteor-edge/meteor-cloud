"""Per-device desired WiFi settings (SSID + encrypted PSK).

Revision ID: 0026_device_wifi_settings
Revises: 0025_deploy_device_desired_rev
Create Date: 2026-11-04 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_device_wifi_settings"
down_revision: str | Sequence[str] | None = "0025_deploy_device_desired_rev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_wifi_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ssid", sa.String(length=32), nullable=False),
        sa.Column("interface", sa.String(length=32), nullable=False, server_default="wlan0"),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("psk_ciphertext", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("applied_ssid", sa.String(length=32), nullable=True),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.Column("sync_status", sa.String(length=32), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", name="uq_device_wifi_settings_device_id"),
    )
    op.create_index(
        "ix_device_wifi_settings_organization_id",
        "device_wifi_settings",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_wifi_settings_organization_id", table_name="device_wifi_settings")
    op.drop_table("device_wifi_settings")
