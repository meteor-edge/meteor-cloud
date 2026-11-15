"""platform_event_outbox: control-plane changes waiting to be sent to the data plane.

Revision ID: 0013_platform_event_outbox
Revises: 0012_applications
Create Date: 2026-11-15 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_platform_event_outbox"
down_revision: str | Sequence[str] | None = "0012_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_event_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_platform_event_outbox_created_at", "platform_event_outbox", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_platform_event_outbox_created_at", table_name="platform_event_outbox")
    op.drop_table("platform_event_outbox")
