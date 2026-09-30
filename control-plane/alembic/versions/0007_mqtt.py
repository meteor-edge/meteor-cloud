"""Add MQTT credentials, commands, and device MQTT status.

Revision ID: 0007_mqtt
Revises: 0006_audit_events
Create Date: 2026-08-20 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_mqtt"
down_revision: str | Sequence[str] | None = "0006_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("mqtt_status", sa.String(length=16), nullable=True))
    op.add_column(
        "devices",
        sa.Column("mqtt_status_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "device_mqtt_credentials",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("password_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_table(
        "device_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_commands_organization_id", "device_commands", ["organization_id"])
    op.create_index("ix_device_commands_device_id", "device_commands", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_device_commands_device_id", table_name="device_commands")
    op.drop_index("ix_device_commands_organization_id", table_name="device_commands")
    op.drop_table("device_commands")
    op.drop_table("device_mqtt_credentials")
    op.drop_column("devices", "mqtt_status_at")
    op.drop_column("devices", "mqtt_status")
