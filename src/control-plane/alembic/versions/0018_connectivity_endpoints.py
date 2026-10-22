"""Device WireGuard connectivity, application endpoints, and shell sessions.

Revision ID: 0018_connectivity_endpoints
Revises: 0017_deployment_provider_ref
Create Date: 2026-10-22 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_connectivity_endpoints"
down_revision: str | Sequence[str] | None = "0017_deployment_provider_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_connectivity",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), server_default="wireguard", nullable=False),
        sa.Column("wg_public_key", sa.String(length=64), nullable=True),
        sa.Column("wg_address", postgresql.INET(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("last_handshake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
        sa.UniqueConstraint("wg_public_key", name="uq_device_connectivity_wg_public_key"),
        sa.UniqueConstraint("wg_address", name="uq_device_connectivity_wg_address"),
    )
    op.create_index("ix_device_connectivity_organization_id", "device_connectivity", ["organization_id"])
    op.create_index("ix_device_connectivity_status", "device_connectivity", ["status"])

    op.create_table(
        "application_endpoints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=True),
        sa.Column("device_software_id", sa.UUID(), nullable=True),
        sa.Column("workload_key", sa.String(length=255), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("protocol", sa.String(length=16), server_default="http", nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("availability", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_software_id"], ["device_software.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hostname", name="uq_application_endpoints_hostname"),
        sa.CheckConstraint("port >= 1 AND port <= 65535", name="ck_application_endpoints_port"),
    )
    op.create_index("ix_application_endpoints_organization_id", "application_endpoints", ["organization_id"])
    op.create_index("ix_application_endpoints_device_id", "application_endpoints", ["device_id"])
    op.create_index(
        "uq_application_endpoints_device_port_enabled",
        "application_endpoints",
        ["device_id", "port"],
        unique=True,
        postgresql_where=sa.text("enabled IS TRUE"),
    )

    op.create_table(
        "device_shell_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_hash", name="uq_device_shell_sessions_ticket_hash"),
    )
    op.create_index("ix_device_shell_sessions_device_id", "device_shell_sessions", ["device_id"])
    op.create_index("ix_device_shell_sessions_organization_id", "device_shell_sessions", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_device_shell_sessions_organization_id", table_name="device_shell_sessions")
    op.drop_index("ix_device_shell_sessions_device_id", table_name="device_shell_sessions")
    op.drop_table("device_shell_sessions")
    op.drop_index("uq_application_endpoints_device_port_enabled", table_name="application_endpoints")
    op.drop_index("ix_application_endpoints_device_id", table_name="application_endpoints")
    op.drop_index("ix_application_endpoints_organization_id", table_name="application_endpoints")
    op.drop_table("application_endpoints")
    op.drop_index("ix_device_connectivity_status", table_name="device_connectivity")
    op.drop_index("ix_device_connectivity_organization_id", table_name="device_connectivity")
    op.drop_table("device_connectivity")
