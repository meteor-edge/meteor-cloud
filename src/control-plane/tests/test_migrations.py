"""Migrations build the schema the models describe (what ``alembic check`` verifies).

Tests create tables with ``Base.metadata.create_all``, so without this a model change
that has no migration, or a migration that disagrees with its model, goes unnoticed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.models import Base
from tests.db_guard import assert_safe_test_database_url, ensure_database_exists

CONTROL_PLANE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def migrations_url() -> str:
    test_url = make_url(get_settings().database_url)
    name = test_url.database.removesuffix("_test") + "_migrations_test"
    url = test_url.set(database=name).render_as_string(hide_password=False)
    assert_safe_test_database_url(url)
    ensure_database_exists(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    return url


def _alembic(url: str, *args: str) -> None:
    # A subprocess, so alembic's logging.fileConfig does not disable this session's loggers.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=CONTROL_PLANE,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


def test_migrations_match_models(migrations_url: str) -> None:
    _alembic(migrations_url, "upgrade", "head")
    engine = create_engine(migrations_url)
    try:
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], "Models and migrations disagree; add a migration or fix the model:\n" + "\n".join(map(str, diff))


def test_latest_migration_downgrades_and_upgrades(migrations_url: str) -> None:
    _alembic(migrations_url, "upgrade", "head")
    _alembic(migrations_url, "downgrade", "-1")
    _alembic(migrations_url, "upgrade", "head")


def test_upgrade_restores_permissions_missing_from_an_older_catalog(migrations_url: str) -> None:
    # Databases seeded before device.shell existed lacked it, so even owners were denied.
    _alembic(migrations_url, "upgrade", "head")
    _alembic(migrations_url, "downgrade", "0021_connectivity_ssh_auth")
    engine = create_engine(migrations_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM permissions WHERE resource = 'device' AND action = 'shell'"))
        _alembic(migrations_url, "upgrade", "head")
        with engine.connect() as connection:
            granted = connection.scalar(
                text(
                    "SELECT count(*) FROM role_permissions rp"
                    " JOIN permissions p ON p.id = rp.permission_id"
                    " JOIN roles r ON r.id = rp.role_id"
                    " WHERE r.key = 'owner' AND r.organization_id IS NULL"
                    " AND p.resource = 'device' AND p.action = 'shell'"
                )
            )
    finally:
        engine.dispose()
    assert granted == 1
