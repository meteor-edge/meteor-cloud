"""Keep pytest away from the development and production databases.

Pytest truncates every table between tests. This module rewrites the connection
to a dedicated ``*_test`` database and refuses to run otherwise.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}
_UNSAFE_DATABASE_NAMES = {"edge_platform", "postgres", "template0", "template1"}


def _env_file_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    backend_dir = here.parents[1]
    return [
        Path.cwd() / ".env",
        backend_dir / ".env",
        repo_root / ".env",
    ]


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _dotenv_value(key: str) -> str | None:
    seen: set[Path] = set()
    for path in _env_file_candidates():
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        value = _parse_env_file(path).get(key, "").strip()
        if value:
            return value
    return None


def rewrite_to_test_database_url(url: str) -> str:
    parsed = make_url(url)
    name = parsed.database or ""
    if name.endswith("_test"):
        return parsed.render_as_string(hide_password=False)
    return parsed.set(database=f"{name}_test").render_as_string(hide_password=False)


def assert_safe_test_database_url(url: str, *, allow_remote: bool | None = None) -> None:
    parsed = make_url(url)
    name = parsed.database or ""
    host = (parsed.host or "").lower()
    if allow_remote is None:
        allow_remote = os.environ.get("ALLOW_REMOTE_TEST_DATABASE", "").lower() in {
            "1",
            "true",
            "yes",
        }

    if not name.endswith("_test") or name in _UNSAFE_DATABASE_NAMES:
        raise RuntimeError(
            f"Refusing to run tests against database {name!r}. "
            "Pytest deletes all rows between tests, so it must use a dedicated "
            "database whose name ends with '_test'. Set TEST_DATABASE_URL."
        )
    if not _DB_NAME_RE.fullmatch(name):
        raise RuntimeError(f"Unsafe test database name {name!r}.")
    if host not in _LOCAL_HOSTS and not allow_remote:
        raise RuntimeError(
            f"Refusing to run tests against host {host!r}. "
            "Tests only use local Postgres (localhost / docker). "
            "Set ALLOW_REMOTE_TEST_DATABASE=1 if you really mean to use a remote test DB."
        )


def resolve_test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL", "").strip() or _dotenv_value("TEST_DATABASE_URL")
    if explicit:
        url = explicit
    else:
        base = os.environ.get("DATABASE_URL", "").strip() or _dotenv_value("DATABASE_URL")
        if not base:
            base = "postgresql+psycopg://edge:edge@localhost:5432/edge_platform"
        url = rewrite_to_test_database_url(base)
    assert_safe_test_database_url(url)
    return url


def apply_test_database_url() -> str:
    url = resolve_test_database_url()
    os.environ["DATABASE_URL"] = url
    return url


def ensure_database_exists(url: str) -> None:
    parsed = make_url(url)
    db_name = parsed.database or ""
    if not _DB_NAME_RE.fullmatch(db_name):
        raise RuntimeError(f"Unsafe test database name {db_name!r}.")

    admin_url = parsed.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                quoted = db_name.replace('"', '""')
                connection.execute(text(f'CREATE DATABASE "{quoted}"'))
    finally:
        engine.dispose()


def pytest_configure(config) -> None:  # noqa: ARG001
    apply_test_database_url()


apply_test_database_url()
