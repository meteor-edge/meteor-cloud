"""artifact.publish: only owners and admins may show OS images in Meteor Imager.

Revision ID: 0003_artifact_publish_permission
Revises: 0002_artifact_published
Create Date: 2026-11-11 14:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_artifact_publish_permission"
down_revision: str | Sequence[str] | None = "0002_artifact_published"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("owner", "admin")


def upgrade() -> None:
    permission_id = uuid.uuid4()
    op.get_bind().execute(
        sa.text(
            "INSERT INTO permissions (id, resource, action, label, description) "
            "VALUES (:id, 'artifact', 'publish', :label, :description)"
        ),
        {
            "id": permission_id,
            "label": "Publish OS images",
            "description": "Show OS images in Meteor Imager, where anyone who can reach the server can download them.",
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
    op.execute("DELETE FROM permissions WHERE resource = 'artifact' AND action = 'publish'")
