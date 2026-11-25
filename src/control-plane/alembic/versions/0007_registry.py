"""Container registry: signing keys, namespaces, tokens and image permissions.

Revision ID: 0007_registry
Revises: 0006_enrollment_auto_approve
Create Date: 2026-11-25 12:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_registry"
down_revision: str | Sequence[str] | None = "0006_enrollment_auto_approve"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the catalog rows (app/authorization/catalog.py).
PERMISSIONS = (
    ("read", "View images", "List and pull container images.", ("owner", "admin", "operator", "developer", "viewer")),
    ("push", "Push images", "Push container images to the organization's registry.", ("owner", "admin", "developer")),
    ("delete", "Delete images", "Delete container images from the organization's registry.", ("owner", "admin")),
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "registry_signing_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("public_jwk", sa.Text(), nullable=False),
        sa.Column("sealed_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("key_id", name="uq_registry_signing_keys_key_id"),
    )
    op.create_table(
        "registry_namespaces",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.UniqueConstraint("organization_id", name="uq_registry_namespaces_organization_id"),
    )
    op.create_table(
        "registry_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("access", sa.String(16), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("repository_prefix", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.UniqueConstraint("token_hash", name="uq_registry_tokens_token_hash"),
        sa.CheckConstraint("(kind = 'personal') = (user_id IS NOT NULL)", name="ck_registry_tokens_kind_user"),
    )
    op.create_index("ix_registry_tokens_organization_id", "registry_tokens", ["organization_id"])
    op.create_index("ix_registry_tokens_user_id", "registry_tokens", ["user_id"])

    bind = op.get_bind()
    for action, label, description, roles in PERMISSIONS:
        permission_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, label, description) "
                "VALUES (:id, 'image', :action, :label, :description)"
            ),
            {"id": permission_id, "action": action, "label": label, "description": description},
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT id, :id FROM roles WHERE is_system AND organization_id IS NULL AND key = ANY(:keys)"
            ),
            {"id": permission_id, "keys": list(roles)},
        )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE resource = 'image'")
    op.drop_table("registry_tokens")
    op.drop_table("registry_namespaces")
    op.drop_table("registry_signing_keys")
