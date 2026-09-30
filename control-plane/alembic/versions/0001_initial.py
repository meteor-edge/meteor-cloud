"""Initial schema baseline.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-29 00:00:00.000000

No application tables yet. This revision establishes the Alembic version table
and a clean baseline for future migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
