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


def test_upgrade_takes_device_shell_away_from_operators(migrations_url: str) -> None:
    _alembic(migrations_url, "upgrade", "head")
    _alembic(migrations_url, "downgrade", "0021_connectivity_ssh_auth")
    operator_shell = (
        "SELECT count(*) FROM role_permissions rp"
        " JOIN permissions p ON p.id = rp.permission_id"
        " JOIN roles r ON r.id = rp.role_id"
        " WHERE r.key = 'operator' AND r.organization_id IS NULL"
        " AND p.resource = 'device' AND p.action = 'shell'"
    )
    engine = create_engine(migrations_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id)"
                    " SELECT r.id, p.id FROM roles r, permissions p"
                    " WHERE r.key = 'operator' AND r.organization_id IS NULL"
                    " AND p.resource = 'device' AND p.action = 'shell'"
                    " ON CONFLICT DO NOTHING"
                )
            )
            assert connection.scalar(text(operator_shell)) == 1
        _alembic(migrations_url, "upgrade", "head")
        with engine.connect() as connection:
            remaining = connection.scalar(text(operator_shell))
    finally:
        engine.dispose()
    assert remaining == 0


def test_activated_at_backfill(migrations_url: str) -> None:
    _alembic(migrations_url, "upgrade", "head")
    _alembic(migrations_url, "downgrade", "0023_device_logs")
    engine = create_engine(migrations_url)
    released_at = "2026-01-02T03:04:05+00:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, full_name, password_hash)"
                    " VALUES ('00000000-0000-0000-0000-0000000000a1', 'mig@example.com', 'Mig', 'x');"
                    "INSERT INTO organizations (id, name, slug, created_by_user_id)"
                    " VALUES ('00000000-0000-0000-0000-0000000000b1', 'Mig', 'mig',"
                    " '00000000-0000-0000-0000-0000000000a1');"
                    "INSERT INTO releases (id, organization_id, name, version, kind)"
                    " VALUES ('00000000-0000-0000-0000-0000000000c1',"
                    " '00000000-0000-0000-0000-0000000000b1', 'r', '1', 'systemd');"
                    "INSERT INTO devices (id, organization_id, name)"
                    " SELECT ('00000000-0000-0000-0000-00000000000' || n)::uuid,"
                    " '00000000-0000-0000-0000-0000000000b1', 'd' || n FROM generate_series(1, 4) n;"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO deployments (id, organization_id, release_id, rollout, created_at)"
                    " VALUES ('00000000-0000-0000-0000-0000000000e1',"
                    " '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000c1',"
                    " CAST(:batched AS jsonb), '2026-01-01T00:00:00+00:00'),"
                    " ('00000000-0000-0000-0000-0000000000e2',"
                    " '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000c1',"
                    " '{\"strategy\": \"immediate\"}', '2026-01-01T00:00:00+00:00')"
                ),
                {
                    "batched": (
                        '{"strategy": "batched", "batch_size": 1, "batch_released_at": "'
                        + released_at
                        + '", "activated_device_ids": ["00000000-0000-0000-0000-000000000001"]}'
                    )
                },
            )
            connection.execute(
                text(
                    "INSERT INTO deployment_devices (id, deployment_id, device_id, status) VALUES"
                    " (gen_random_uuid(), '00000000-0000-0000-0000-0000000000e1',"
                    " '00000000-0000-0000-0000-000000000001', 'succeeded'),"
                    " (gen_random_uuid(), '00000000-0000-0000-0000-0000000000e1',"
                    " '00000000-0000-0000-0000-000000000002', 'queued'),"
                    " (gen_random_uuid(), '00000000-0000-0000-0000-0000000000e2',"
                    " '00000000-0000-0000-0000-000000000003', 'pending'),"
                    " (gen_random_uuid(), '00000000-0000-0000-0000-0000000000e2',"
                    " '00000000-0000-0000-0000-000000000004', 'skipped')"
                )
            )
        _alembic(migrations_url, "upgrade", "head")
        with engine.connect() as connection:
            rows = dict(
                connection.execute(
                    text(
                        "SELECT device_id::text, to_char(activated_at AT TIME ZONE 'UTC',"
                        " 'YYYY-MM-DD HH24:MI:SS') FROM deployment_devices"
                    )
                ).all()
            )
    finally:
        engine.dispose()
    assert rows == {
        "00000000-0000-0000-0000-000000000001": "2026-01-02 03:04:05",
        "00000000-0000-0000-0000-000000000002": None,
        "00000000-0000-0000-0000-000000000003": "2026-01-01 00:00:00",
        "00000000-0000-0000-0000-000000000004": None,
    }
