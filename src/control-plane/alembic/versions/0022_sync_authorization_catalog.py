"""Add device.shell and endpoint.* to the seeded catalog and system roles.

The catalog is only written by migrations, so databases created before these
permissions existed never received them and every role was denied.

Revision ID: 0022_sync_authorization_catalog
Revises: 0021_connectivity_ssh_auth
Create Date: 2026-10-24 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_sync_authorization_catalog"
down_revision: str | Sequence[str] | None = "0021_connectivity_ssh_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from sqlalchemy.orm import Session

    from app.authorization.seed import seed_authorization_catalog

    session = Session(bind=op.get_bind())
    seed_authorization_catalog(session)
    session.commit()


def downgrade() -> None:
    # Earlier revisions do not reference these rows; leaving them is harmless.
    pass
