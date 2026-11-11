"""device_wifi_networks, device_wifi_status and the device.network permission: Wi-Fi changes from the console.

Revision ID: 0006_device_wifi
Revises: 0005_artifact_device_types
Create Date: 2026-11-12 00:30:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_device_wifi"
down_revision: str | Sequence[str] | None = "0005_artifact_device_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A wrong network can take a device offline: owners and admins only.
ROLES = ("owner", "admin")


def upgrade() -> None:
    op.create_table(
        "device_wifi_networks",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ssid", sa.String(length=64), nullable=False),
        sa.Column("security", sa.String(length=16), nullable=False),
        sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("connect_now", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("password_sealed", sa.LargeBinary(), nullable=True),
        sa.Column("password_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "ssid", name="uq_device_wifi_networks_device_ssid"),
    )
    op.create_index("ix_device_wifi_networks_device_id", "device_wifi_networks", ["device_id"])
    op.create_table(
        "device_wifi_status",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supported", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("unsupported_reason", sa.String(length=255), nullable=True),
        sa.Column("connected_ssid", sa.String(length=64), nullable=True),
        sa.Column("signal_percent", sa.Integer(), nullable=True),
        sa.Column("interface", sa.String(length=32), nullable=True),
        sa.Column("wired_connected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("saved_networks", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )

    permission_id = uuid.uuid4()
    op.get_bind().execute(
        sa.text(
            "INSERT INTO permissions (id, resource, action, label, description) "
            "VALUES (:id, 'device', 'network', :label, :description)"
        ),
        {
            "id": permission_id,
            "label": "Change device Wi-Fi",
            "description": "Add, change and remove the Wi-Fi networks of a device. A wrong network can take it offline.",
        },
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT id, :permission_id FROM roles WHERE is_system AND organization_id IS NULL AND key IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"permission_id": permission_id, "keys": list(ROLES)},
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE resource = 'device' AND action = 'network'")
    op.drop_table("device_wifi_status")
    op.drop_index("ix_device_wifi_networks_device_id", table_name="device_wifi_networks")
    op.drop_table("device_wifi_networks")
