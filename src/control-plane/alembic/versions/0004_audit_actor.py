"""audit_events.actor_type/actor_id: record devices, enrollment keys and apps as actors, not only users.

Revision ID: 0004_audit_actor
Revises: 0003_artifact_publish_permission
Create Date: 2026-11-12 05:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_audit_actor"
down_revision: str | Sequence[str] | None = "0003_artifact_publish_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("actor_type", sa.String(length=32), nullable=True))
    op.add_column("audit_events", sa.Column("actor_id", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE audit_events SET actor_type = 'user', actor_id = actor_user_id::text WHERE actor_user_id IS NOT NULL"
    )
    op.create_index("ix_audit_events_resource", "audit_events", ["resource_type", "resource_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource", table_name="audit_events")
    op.drop_column("audit_events", "actor_id")
    op.drop_column("audit_events", "actor_type")
