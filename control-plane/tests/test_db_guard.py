"""Guards that keep pytest off the application database."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from tests.db_guard import assert_safe_test_database_url, rewrite_to_test_database_url


def test_get_settings_uses_test_database() -> None:
    database = get_settings().database_url.rsplit("/", 1)[-1]
    assert database.endswith("_test")
    assert database != "edge_platform"


def test_rewrites_application_database_to_test_suffix() -> None:
    url = rewrite_to_test_database_url("postgresql+psycopg://edge:edge@localhost:5432/edge_platform")
    assert url.endswith("/edge_platform_test")
    assert "edge_platform_test" in url
    assert url.rstrip("/").endswith("_test")


def test_leaves_test_database_url_unchanged() -> None:
    url = "postgresql+psycopg://edge:edge@localhost:5432/edge_platform_test"
    rewritten = rewrite_to_test_database_url(url)
    assert rewritten.rstrip("/").endswith("/edge_platform_test")


def test_session_engine_is_not_application_database(engine) -> None:
    assert engine.url.database is not None
    assert engine.url.database.endswith("_test")
    assert engine.url.database != "edge_platform"


def test_refuses_application_database_name() -> None:
    with pytest.raises(RuntimeError, match="edge_platform"):
        assert_safe_test_database_url(
            "postgresql+psycopg://edge:edge@localhost:5432/edge_platform",
            allow_remote=False,
        )


def test_refuses_remote_host() -> None:
    with pytest.raises(RuntimeError, match="prod.example"):
        assert_safe_test_database_url(
            "postgresql+psycopg://edge:edge@prod.example:5432/edge_platform_test",
            allow_remote=False,
        )


def test_allows_explicit_remote_test_database() -> None:
    assert_safe_test_database_url(
        "postgresql+psycopg://edge:edge@prod.example:5432/edge_platform_test",
        allow_remote=True,
    )
